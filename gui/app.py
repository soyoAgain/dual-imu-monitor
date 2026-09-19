#!/usr/bin/env python3
"""Mac GUI for real-time dual-IMU samples from STM32MP157 (MPU6050 + ICM20608).

Single SSH stream from tools/monitor_dual_imu.py; MPU6050 Y-axis saturation is
handled by the guarded fusion state machine in tools/fusion_state.py. The main
window keeps the previous controls and shows six plots in a 3x2 grid.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QPlainTextEdit, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from fusion_state import (  # noqa: E402
    FusionStateMachine, STATE_BLENDED, STATE_ICM_FALLBACK, STATE_INVALID,
    STATE_MPU_PRIMARY, transform_icm_accel,
)

matplotlib.rcParams["font.sans-serif"] = [
    "STHeiti", "PingFang HK", "Arial Unicode MS", "Hiragino Sans GB",
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans",
]

DEFAULT_CALIBRATION = (PROJECT_ROOT / "diagnostic_logs" / "phase6_accel_calibration"
                       / "accel_calibration.json")
STATE_TEXT = {
    STATE_MPU_PRIMARY: "MPU",
    STATE_BLENDED: "融合",
    STATE_ICM_FALLBACK: "ICM 补偿",
    STATE_INVALID: "无效",
}
STATE_COLOR = {
    STATE_MPU_PRIMARY: "#2563eb",
    STATE_BLENDED: "#16a34a",
    STATE_ICM_FALLBACK: "#d97706",
    STATE_INVALID: "#dc2626",
}
TRAJECTORY_HALF_RANGE_M = 0.05

DUAL_RE = re.compile(
    r"seq=(?P<seq>\d+) t=(?P<t>[\d.]+) "
    r"mpu_a=(?P<max>[-\d.]+),(?P<may>[-\d.]+),(?P<maz>[-\d.]+) "
    r"mpu_g=(?P<mgx>[-\d.]+),(?P<mgy>[-\d.]+),(?P<mgz>[-\d.]+) "
    r"icm_a=(?P<iax>[-\d.]+),(?P<iay>[-\d.]+),(?P<iaz>[-\d.]+) "
    r"icm_g=(?P<igx>[-\d.]+),(?P<igy>[-\d.]+),(?P<igz>[-\d.]+) "
    r"mpu_t=(?P<mtemp>[-\d.]+) icm_t=(?P<itemp>[-\d.]+) "
    r"age_icm_ms=(?P<age>[-\d.]+) mpu_sat=(?P<sat>\d+) "
    r"errors=(?P<i2c>\d+)/(?P<tx>\d+)/(?P<spi>\d+)/(?P<sync>\d+)"
)
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass
class Sample:
    seq: int
    timestamp: float
    mpu_accel: tuple
    mpu_gyro: tuple
    icm_accel: tuple
    icm_gyro: tuple
    temperature: float
    icm_temperature: float
    i2c_errors: int
    tx_errors: int
    spi_errors: int
    sync_errors: int
    icm_age_ms: float
    saturation_mask: int
    accel: tuple | None = None
    gyro: tuple | None = None
    icm_accel_mpu: tuple | None = None
    icm_gyro_mpu: tuple | None = None
    fusion_state: str = STATE_MPU_PRIMARY
    icm_valid: bool = False


def parse_sample(line: str) -> Sample | None:
    match = DUAL_RE.search(ANSI_RE.sub("", line))
    if not match:
        return None
    v = match.groupdict()

    def triple(prefix):
        return tuple(float(v[prefix + axis]) for axis in ("x", "y", "z"))

    return Sample(
        seq=int(v["seq"]), timestamp=float(v["t"]),
        mpu_accel=triple("ma"), mpu_gyro=triple("mg"),
        icm_accel=triple("ia"), icm_gyro=triple("ig"),
        temperature=float(v["mtemp"]), icm_temperature=float(v["itemp"]),
        i2c_errors=int(v["i2c"]), tx_errors=int(v["tx"]),
        spi_errors=int(v["spi"]), sync_errors=int(v["sync"]),
        icm_age_ms=float(v["age"]), saturation_mask=int(v["sat"]),
    )


GRAVITY = 9.80665


def quaternion_from_gravity(accel):
    """Initial quaternion (body -> world) with zero yaw from a static accelerometer sample."""
    ax, ay, az = accel
    roll = math.atan2(ay, az)
    pitch = math.atan2(-ax, math.hypot(ay, az))
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    return np.array([cr * cp, sr * cp, cr * sp, -sr * sp], dtype=float)


def quaternion_normalize(q):
    return q / max(float(np.linalg.norm(q)), 1e-12)


def integrate_gyro(q, gyro, dt):
    """Pure gyro integration (no gravity correction); gyro in rad/s."""
    qw, qx, qy, qz = q
    gx, gy, gz = gyro
    qdot = np.array([
        -0.5 * (qx * gx + qy * gy + qz * gz),
        0.5 * (qw * gx + qy * gz - qz * gy),
        0.5 * (qw * gy - qx * gz + qz * gx),
        0.5 * (qw * gz + qx * gy - qy * gx),
    ])
    return quaternion_normalize(q + qdot * dt)


def update_quaternion(q, gyro, accel, dt, kp):
    """Mahony-style gravity correction; gyro in rad/s, accel in m/s^2."""
    norm = float(np.linalg.norm(accel))
    if norm < 1e-6:
        return q
    ax, ay, az = accel / norm
    qw, qx, qy, qz = q
    vx = 2.0 * (qx * qz - qw * qy)
    vy = 2.0 * (qw * qx + qy * qz)
    vz = qw * qw - qx * qx - qy * qy + qz * qz
    ex = ay * vz - az * vy
    ey = az * vx - ax * vz
    ez = ax * vy - ay * vx
    corrected = (gyro[0] + kp * ex, gyro[1] + kp * ey, gyro[2] + kp * ez)
    return integrate_gyro(q, corrected, dt)


def rotation_matrix(q):
    """Rotation matrix body -> world from a unit quaternion."""
    qw, qx, qy, qz = q
    return np.array([
        [1.0 - 2.0 * (qy * qy + qz * qz), 2.0 * (qx * qy - qw * qz), 2.0 * (qx * qz + qw * qy)],
        [2.0 * (qx * qy + qw * qz), 1.0 - 2.0 * (qx * qx + qz * qz), 2.0 * (qy * qz - qw * qx)],
        [2.0 * (qx * qz - qw * qy), 2.0 * (qy * qz + qw * qx), 1.0 - 2.0 * (qx * qx + qy * qy)],
    ])


def euler_degrees(q):
    qw, qx, qy, qz = q
    roll = math.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (qw * qy - qz * qx))))
    yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


class StreamWorker(QThread):
    sample_received = pyqtSignal(object)
    log_received = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, host: str, interface: str, reader: Path,
                 auto_start_m4: bool = True, demo: bool = False,
                 rotation=None, accel_bias=None, mpu_accel_bias=None):
        super().__init__()
        self.host, self.interface, self.reader = host, interface, reader
        self.auto_start_m4, self.demo = auto_start_m4, demo
        self.rotation = np.asarray(rotation if rotation is not None else np.eye(3), dtype=float)
        self.accel_bias = np.asarray(accel_bias if accel_bias is not None else np.zeros(3), dtype=float)
        self.mpu_accel_bias = np.asarray(mpu_accel_bias if mpu_accel_bias is not None
                                         else np.zeros(3), dtype=float)
        self.process: subprocess.Popen | None = None
        self.control_path = f"/tmp/mp157-gui-{os.getpid()}-{id(self):x}"
        self.master_started = False
        self._stopping = False

    def run(self):
        if self.demo:
            self._run_demo()
            return
        try:
            master = subprocess.run(
                [
                    "ssh", "-MNf", "-o", "ControlMaster=yes",
                    "-o", f"ControlPath={self.control_path}",
                    "-o", "ControlPersist=60",
                    "-o", f"BindInterface={self.interface}",
                    "-o", "ConnectTimeout=5", self.host,
                ],
                capture_output=True, text=True, timeout=10,
            )
            if master.returncode:
                raise RuntimeError(master.stderr.strip() or "SSH 主连接建立失败")
            self.master_started = True
            self.log_received.emit("SSH 主连接已建立，后续操作复用同一连接")
            if self.auto_start_m4:
                self.status_changed.emit("正在配置 Linux 开机启动 M4…")
                installer = self.reader.parent / "install_m4_autostart.sh"
                installed = subprocess.run(
                    [str(installer), self.host, self.interface, self.control_path],
                    capture_output=True, text=True, timeout=45,
                )
                if installed.returncode:
                    raise RuntimeError(installed.stderr.strip() or "M4 开机启动配置失败")
                self.log_received.emit(
                    "M4 开机启动已启用：" + " ".join(installed.stdout.splitlines()[-4:]))
            self.status_changed.emit("正在上传采集脚本…")
            result = subprocess.run(
                ["scp", "-o", f"BindInterface={self.interface}",
                 "-o", f"ControlPath={self.control_path}",
                 "-o", "ConnectTimeout=5", str(self.reader), f"{self.host}:/tmp/"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "SCP 失败")
            self.log_received.emit(f"已上传 {self.reader.name} 到 {self.host}:/tmp/")
            probe = subprocess.run(
                [
                    "ssh", "-o", f"BindInterface={self.interface}",
                    "-o", f"ControlPath={self.control_path}",
                    "-o", "ConnectTimeout=5", self.host,
                    "r=/sys/class/remoteproc/remoteproc0; "
                    "printf '%s %s ' \"$(cat $r/state)\" \"$(cat $r/firmware)\"; "
                    "ls -l /dev/icm20608 2>/dev/null | wc -l",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if probe.returncode:
                raise RuntimeError(probe.stderr.strip() or "无法读取 M4 固件状态")
            fields = probe.stdout.strip().split()
            state = fields[0] if fields else "unknown"
            firmware = fields[1] if len(fields) > 1 else "unknown"
            icm_present = (fields[2] == "1") if len(fields) > 2 else False
            self.log_received.emit(f"M4 状态：{state}，固件：{firmware}，"
                                   f"ICM20608 设备：{'存在' if icm_present else '缺失'}")
            accepted_firmware = {"m4_rpmsg.elf", "m4_rpmsg_fix1.elf"}
            if state != "running" or firmware not in accepted_firmware:
                raise RuntimeError(
                    "当前不是 MPU6050 RPMsg 采样固件。请先关闭 GUI，执行 "
                    "sh tools/start_rpmsg_m4.sh；确认固件为 m4_rpmsg.elf 后再开始。")
            if not icm_present:
                raise RuntimeError(
                    "未找到 /dev/icm20608，请先在开发板上执行 sh tools/install_icm20608.sh。")
            self.status_changed.emit("已连接，等待双传感器数据…")
            command = [
                "ssh", "-tt", "-o", f"BindInterface={self.interface}",
                "-o", f"ControlPath={self.control_path}",
                "-o", "ConnectTimeout=5", self.host,
                "python3 -u /tmp/monitor_dual_imu.py",
            ]
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE, text=True, bufsize=1, start_new_session=True,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                if self._stopping:
                    break
                sample = parse_sample(line)
                if sample:
                    self.sample_received.emit(sample)
                elif line.strip():
                    self.log_received.emit(ANSI_RE.sub("", line).rstrip())
            code = self.process.wait()
            if code and not self._stopping:
                raise RuntimeError(f"SSH 数据流退出，状态码 {code}")
        except Exception as exc:
            self.log_received.emit(f"错误：{exc}")
            self.status_changed.emit("连接失败")
        finally:
            self.process = None
            if self.master_started:
                subprocess.run(
                    ["ssh", "-O", "exit", "-o", f"ControlPath={self.control_path}", self.host],
                    capture_output=True, timeout=5,
                )
                self.master_started = False
            if self._stopping:
                self.status_changed.emit("已停止")

    def _run_demo(self):
        self.status_changed.emit("演示模式 · 50 Hz 双传感器")
        self.log_received.emit("使用模拟双传感器数据，不连接开发板")
        seq, timestamp = 0, 0.0
        while not self._stopping:
            timestamp += 0.02
            seq += 1
            phase = timestamp % 14.0
            saturated = 10.0 <= phase < 12.0
            true_accel = np.array([
                0.6 * np.sin(0.5 * timestamp),
                0.4 * np.cos(0.7 * timestamp),
                9.70 + 0.2 * np.sin(0.4 * timestamp),
            ])
            mpu_accel = true_accel + self.mpu_accel_bias
            if saturated:
                mpu_accel[1] = 19.6127
            mpu_gyro = np.array([
                12.0 * np.sin(0.5 * timestamp),
                8.0 * np.cos(0.4 * timestamp),
                4.0 * np.sin(0.2 * timestamp),
            ])
            icm_accel = self.rotation.T @ true_accel + self.accel_bias
            icm_gyro = self.rotation.T @ mpu_gyro
            sample = Sample(
                seq=seq, timestamp=timestamp,
                mpu_accel=tuple(mpu_accel), mpu_gyro=tuple(mpu_gyro),
                icm_accel=tuple(icm_accel), icm_gyro=tuple(icm_gyro),
                temperature=29.1 + 0.2 * np.sin(0.1 * timestamp),
                icm_temperature=34.0 + 0.1 * np.sin(0.1 * timestamp),
                i2c_errors=0, tx_errors=0, spi_errors=0, sync_errors=0,
                icm_age_ms=1.0, saturation_mask=(2 if saturated else 0),
            )
            self.sample_received.emit(sample)
            self.msleep(20)
        self.status_changed.emit("已停止")

    def stop(self):
        self._stopping = True
        if self.process and self.process.poll() is None:
            try:
                if self.process.stdin:
                    self.process.stdin.write("\x03")
                    self.process.stdin.flush()
                self.process.wait(timeout=2.0)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


class PlotCanvas(FigureCanvasQTAgg):
    def __init__(self):
        self.figure = Figure(figsize=(11, 10), facecolor="#f5f7fa")
        super().__init__(self.figure)
        grid = self.figure.add_gridspec(3, 2, height_ratios=(1, 1.25, 1))
        self.accel_ax = self.figure.add_subplot(grid[0, 0])
        self.gyro_ax = self.figure.add_subplot(grid[0, 1])
        self.path_ax = self.figure.add_subplot(grid[1, 0], projection="3d")
        self.att_ax = self.figure.add_subplot(grid[1, 1], projection="3d")
        self.icm_gyro_ax = self.figure.add_subplot(grid[2, 0])
        self.icm_accel_ax = self.figure.add_subplot(grid[2, 1])
        colors = ("#ef4444", "#22a06b", "#2563eb")
        self.accel_lines = [self.accel_ax.plot([], [], color=c, label=a)[0]
                            for a, c in zip("XYZ", colors)]
        self.gyro_lines = [self.gyro_ax.plot([], [], color=c, label=a)[0]
                           for a, c in zip("XYZ", colors)]
        self.icm_gyro_lines = [self.icm_gyro_ax.plot([], [], color=c, label=a)[0]
                               for a, c in zip("XYZ", colors)]
        self.icm_accel_lines = [self.icm_accel_ax.plot([], [], color=c, label=a)[0]
                                for a, c in zip("XYZ", colors)]
        # raw-mode overlays, hidden by default
        self.raw_accel_lines = [self.accel_ax.plot([], [], color=c, linestyle="--",
                                                   linewidth=1.0, alpha=0.85,
                                                   label=name)[0]
                                for name, c in (("MPU Y", "#f59e0b"), ("ICM→MPU Y", "#7c3aed"))]
        self.raw_gyro_lines = [self.gyro_ax.plot([], [], color=c, linestyle="--",
                                                 linewidth=1.0, alpha=0.8, label=name)[0]
                               for name, c in (("ICM X", "#f59e0b"), ("ICM Y", "#7c3aed"),
                                               ("ICM Z", "#0ea5e9"))]
        self.raw_icm_accel_lines = [self.icm_accel_ax.plot([], [], color=c, linestyle="--",
                                                           linewidth=1.0, alpha=0.8,
                                                           label=name)[0]
                                    for name, c in (("→MPU X", "#f59e0b"), ("→MPU Y", "#7c3aed"),
                                                    ("→MPU Z", "#0ea5e9"))]
        for lines in (self.raw_accel_lines, self.raw_gyro_lines, self.raw_icm_accel_lines):
            for line in lines:
                line.set_visible(False)
        self.path_line = self.path_ax.plot([], [], [], color="#7c3aed", linewidth=1.8)[0]
        self.path_head = self.path_ax.scatter([], [], [], color="#f59e0b", s=35)
        for axis, title, unit in (
            (self.accel_ax, "图1　融合三轴加速度与时间", "加速度 / m/s²"),
            (self.gyro_ax, "图2　三轴角速度与时间", "角速度 / °/s"),
            (self.icm_gyro_ax, "图5　ICM20608 三轴角速度", "角速度 / °/s"),
            (self.icm_accel_ax, "图6　ICM20608 三轴加速度", "加速度 / m/s²"),
        ):
            axis.set_title(title, loc="left", fontsize=10, fontweight="bold")
            axis.set_ylabel(unit); axis.set_xlabel("时间 / s")
            axis.grid(True, alpha=0.25); axis.legend(loc="upper right", ncol=3, fontsize=8)
        self.path_ax.set_title("图3　传感器空间轨迹（积分估计）", loc="left", fontsize=10, fontweight="bold")
        self.path_ax.set_xlabel("X / m"); self.path_ax.set_ylabel("Y / m"); self.path_ax.set_zlabel("Z / m")
        self.path_ax.set_xlim(-TRAJECTORY_HALF_RANGE_M, TRAJECTORY_HALF_RANGE_M)
        self.path_ax.set_ylim(-TRAJECTORY_HALF_RANGE_M, TRAJECTORY_HALF_RANGE_M)
        self.path_ax.set_zlim(-TRAJECTORY_HALF_RANGE_M, TRAJECTORY_HALF_RANGE_M)
        self.path_ax.set_box_aspect((1, 1, 1))
        self.path_ax.text2D(0.99, 0.97, "融合加速度 + 重力补偿 + ZUPT", transform=self.path_ax.transAxes,
                            ha="right", color="#d97706", fontsize=8)
        self._build_attitude_axes()
        # 固定边距，避免 tight_layout 随刻度标签宽度变化导致子图忽大忽小。
        self.figure.subplots_adjust(left=0.06, right=0.98, top=0.95, bottom=0.055,
                                    wspace=0.22, hspace=0.38)

    def _build_attitude_axes(self):
        ax = self.att_ax
        ax.set_title("图4　传感器姿态（纯陀螺积分 · 会漂移）", loc="left", fontsize=10, fontweight="bold")
        ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z")
        ax.set_xlim(-1.15, 1.15); ax.set_ylim(-1.15, 1.15); ax.set_zlim(-1.15, 1.15)
        ax.set_box_aspect((1, 1, 1))
        ax.grid(True, alpha=0.2)
        ax.text2D(0.99, 0.97, "初始朝向来自标定 · 会漂移", transform=ax.transAxes,
                  ha="right", color="#d97706", fontsize=8)
        for vec in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            ax.plot([0.0, vec[0]], [0.0, vec[1]], [0.0, vec[2]], color="#9ca3af", linewidth=1.0, alpha=0.6)
        ax.plot([0.0, 0.0], [0.0, 0.0], [0.0, -1.1], color="#f59e0b", linewidth=1.2,
                linestyle="--", alpha=0.8)
        u = np.linspace(0.0, 2.0 * np.pi, 13); v = np.linspace(0.0, np.pi, 7)
        ax.plot_wireframe(np.outer(np.cos(u), np.sin(v)), np.outer(np.sin(u), np.sin(v)),
                          np.outer(np.ones_like(u), np.cos(v)), color="#cbd5e1", linewidth=0.5, alpha=0.3)
        self.att_axis_lines = [ax.plot([], [], [], color=c, linewidth=2.2)[0]
                               for c in ("#ef4444", "#22a06b", "#2563eb")]
        self.att_normal = ax.plot([], [], [], color="#7c3aed", linewidth=2.6)[0]
        angles = np.linspace(0.0, 2.0 * np.pi, 49)
        self.att_disc_body = np.column_stack((0.45 * np.cos(angles), 0.45 * np.sin(angles),
                                              np.zeros_like(angles)))
        self.att_disc = ax.plot([], [], [], color="#a78bfa", linewidth=1.4, alpha=0.85)[0]

    @staticmethod
    def _autoscale(axis, x, values):
        if len(x) < 2:
            return
        axis.set_xlim(x[0], x[-1] if x[-1] > x[0] else x[0] + 1)
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
        margin = max((high - low) * 0.12, 0.1)
        axis.set_ylim(low - margin, high + margin)

    def update_attitude(self, attitude):
        if attitude is None:
            return
        rotation = rotation_matrix(attitude)
        for index in range(3):
            vec = rotation[:, index]
            self.att_axis_lines[index].set_data_3d([0.0, vec[0]], [0.0, vec[1]], [0.0, vec[2]])
        normal = rotation[:, 2]
        self.att_normal.set_data_3d([0.0, normal[0]], [0.0, normal[1]], [0.0, normal[2]])
        disc = self.att_disc_body @ rotation.T
        self.att_disc.set_data_3d(disc[:, 0], disc[:, 1], disc[:, 2])

    @staticmethod
    def _columns(samples, getter):
        values = [getter(sample) for sample in samples]
        return np.array([[np.nan] * 3 if value is None else list(value) for value in values],
                        dtype=float)

    def update_data(self, samples, positions, attitude=None, raw_mode=False):
        if not samples:
            return
        t = np.array([s.timestamp for s in samples])
        fused_accel = self._columns(samples, lambda s: s.accel)
        mpu_accel = self._columns(samples, lambda s: s.mpu_accel)
        mpu_gyro = self._columns(samples, lambda s: s.mpu_gyro)
        icm_accel = self._columns(samples, lambda s: s.icm_accel)
        icm_gyro = self._columns(samples, lambda s: s.icm_gyro)
        icm_accel_mpu = self._columns(samples, lambda s: s.icm_accel_mpu)
        icm_gyro_mpu = self._columns(samples, lambda s: s.icm_gyro_mpu)
        for index in range(3):
            self.accel_lines[index].set_data(t, fused_accel[:, index])
            self.gyro_lines[index].set_data(t, mpu_gyro[:, index])
            self.icm_gyro_lines[index].set_data(t, icm_gyro[:, index])
            self.icm_accel_lines[index].set_data(t, icm_accel[:, index])
        self.raw_accel_lines[0].set_data(t, mpu_accel[:, 1])
        self.raw_accel_lines[1].set_data(t, icm_accel_mpu[:, 1])
        for index in range(3):
            self.raw_gyro_lines[index].set_data(t, icm_gyro_mpu[:, index])
            self.raw_icm_accel_lines[index].set_data(t, icm_accel_mpu[:, index])
        for lines in (self.raw_accel_lines, self.raw_gyro_lines, self.raw_icm_accel_lines):
            for line in lines:
                line.set_visible(raw_mode)
        self._autoscale(self.accel_ax, t, fused_accel)
        self._autoscale(self.gyro_ax, t, mpu_gyro)
        self._autoscale(self.icm_gyro_ax, t, icm_gyro)
        self._autoscale(self.icm_accel_ax, t, icm_accel)
        if positions:
            xyz = np.array(positions)
            self.path_line.set_data(xyz[:, 0], xyz[:, 1])
            self.path_line.set_3d_properties(xyz[:, 2])
            self.path_head._offsets3d = (
                np.array([xyz[-1, 0]]), np.array([xyz[-1, 1]]), np.array([xyz[-1, 2]]),
            )
            self.path_ax.set_xlim(-TRAJECTORY_HALF_RANGE_M, TRAJECTORY_HALF_RANGE_M)
            self.path_ax.set_ylim(-TRAJECTORY_HALF_RANGE_M, TRAJECTORY_HALF_RANGE_M)
            self.path_ax.set_zlim(-TRAJECTORY_HALF_RANGE_M, TRAJECTORY_HALF_RANGE_M)
        self.update_attitude(attitude)
        self.draw_idle()


class MainWindow(QMainWindow):
    def __init__(self, demo: bool = False):
        super().__init__()
        self.demo = demo
        self.worker: StreamWorker | None = None
        self.samples = deque(maxlen=5000)
        self.positions = deque(maxlen=2500)
        self.attitude_samples = []
        self.attitude_ready = False
        self.gravity = GRAVITY
        self.accel_baseline = None
        self.gyro_bias = np.zeros(3)
        self.quaternion = None
        self.gyro_quaternion = None
        self.filtered_accel = None
        self.filtered_gyro = np.zeros(3)
        self.static_counter = 0
        self.is_static = False
        self.velocity = np.zeros(3); self.position = np.zeros(3)
        self.last_timestamp = None
        self.last_spi_errors = 0
        self.last_sync_errors = 0
        self.rotation = np.eye(3)
        self.icm_accel_bias = np.zeros(3)
        self.mpu_accel_bias = np.zeros(3)
        self.calibration_loaded = False
        self.calibration_text = "未加载"
        self.fusion = FusionStateMachine()
        self._closing = False
        self.setWindowTitle("MP157 · MPU6050 + ICM20608 双传感器监视器")
        self.resize(1560, 1020)
        self._load_calibration()
        self._build_ui()
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_plots); self.timer.start(100)
        if demo:
            QTimer.singleShot(200, self.start_stream)

    def _load_calibration(self):
        path = DEFAULT_CALIBRATION
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.rotation = np.array(data["rotation_icm_to_mpu"], dtype=float)
            self.icm_accel_bias = np.array(data.get("icm_accel_bias_mps2", [0.0, 0.0, 0.0]),
                                           dtype=float)
            metrics = data.get("metrics", {})
            self.mpu_accel_bias = np.array(metrics.get("mpu_accel_bias_mps2", [0.0, 0.0, 0.0]),
                                           dtype=float)
            validation = data.get("validation") or {}
            correlations = validation.get("correlation", [])
            correlation_text = ("，验证相关 " +
                                "/".join(f"{value:.3f}" for value in correlations)) if correlations else ""
            self.calibration_text = (
                f"已加载 {path.name}（时延 {data.get('time_offset_ms', 0.0):.1f} ms，"
                f"重力 RMSE {metrics.get('gravity_rmse_mps2', float('nan')):.3f} m/s²"
                f"{correlation_text}）")
            self.calibration_loaded = True
        except Exception as exc:
            self.calibration_loaded = False
            self.calibration_text = f"未加载（{exc}）"

    def _build_ui(self):
        splitter = QSplitter(); self.setCentralWidget(splitter)
        left = QWidget(); left.setMinimumWidth(330); left.setMaximumWidth(460)
        layout = QVBoxLayout(left)
        config = QGroupBox("连接与配置"); form = QFormLayout(config)
        self.host = QLineEdit("mp157")
        self.interface = QLineEdit("en7")
        self.window_seconds = QSpinBox(); self.window_seconds.setRange(2, 300); self.window_seconds.setValue(20); self.window_seconds.setSuffix(" s")
        self.refresh_ms = QSpinBox(); self.refresh_ms.setRange(40, 2000); self.refresh_ms.setValue(100); self.refresh_ms.setSuffix(" ms")
        self.damping = QDoubleSpinBox(); self.damping.setRange(0.0, 1.0); self.damping.setDecimals(4); self.damping.setSingleStep(0.001); self.damping.setValue(0.985)
        self.alpha_spin = QDoubleSpinBox(); self.alpha_spin.setRange(0.01, 1.0); self.alpha_spin.setDecimals(2); self.alpha_spin.setSingleStep(0.05); self.alpha_spin.setValue(0.2)
        self.kp_spin = QDoubleSpinBox(); self.kp_spin.setRange(0.0, 5.0); self.kp_spin.setDecimals(2); self.kp_spin.setSingleStep(0.1); self.kp_spin.setValue(1.5)
        self.gravity_check = QCheckBox("启用四元数重力补偿"); self.gravity_check.setChecked(True)
        self.zupt_check = QCheckBox("启用零速修正 (ZUPT)"); self.zupt_check.setChecked(True)
        self.raw_check = QCheckBox("显示原始数据（叠加 MPU/ICM）")
        self.auto_start_m4 = QCheckBox("自动启动 M4，并启用 Linux 开机启动")
        self.auto_start_m4.setChecked(True)
        form.addRow("SSH 主机", self.host); form.addRow("有线接口", self.interface)
        form.addRow("显示窗口", self.window_seconds)
        form.addRow("刷新间隔", self.refresh_ms); form.addRow("速度阻尼", self.damping)
        form.addRow("滤波系数 α", self.alpha_spin); form.addRow("姿态增益 kp", self.kp_spin)
        form.addRow(self.gravity_check); form.addRow(self.zupt_check)
        form.addRow(self.raw_check); form.addRow(self.auto_start_m4)
        layout.addWidget(config)
        row = QHBoxLayout()
        self.start_button = QPushButton("开始"); self.stop_button = QPushButton("停止"); self.clear_button = QPushButton("清空")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_stream); self.stop_button.clicked.connect(self.stop_stream); self.clear_button.clicked.connect(self.clear_data)
        for button in (self.start_button, self.stop_button, self.clear_button): row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel("未连接"); self.status.setStyleSheet("font-weight: 600; color: #2563eb; padding: 5px")
        self.fusion_label = QLabel("融合状态：—")
        self.fusion_label.setStyleSheet("font-weight: 600; padding: 2px")
        self.icm_label = QLabel("ICM：—")
        self.icm_label.setStyleSheet("font-family: Menlo, monospace; padding: 2px")
        self.calibration_label = QLabel("标定：" + self.calibration_text)
        self.calibration_label.setWordWrap(True)
        self.calibration_label.setStyleSheet("color: #475569; padding: 2px")
        self.metrics = QLabel("seq: —\n频率: —\n温度: —\nI²C/TX 错误: —")
        self.metrics.setStyleSheet("font-family: Menlo, monospace; padding: 5px")
        layout.addWidget(self.status); layout.addWidget(self.fusion_label)
        layout.addWidget(self.icm_label); layout.addWidget(self.calibration_label)
        layout.addWidget(self.metrics)
        layout.addWidget(QLabel("控制台"))
        self.console = QPlainTextEdit(); self.console.setReadOnly(True); self.console.setStyleSheet("background:#111827;color:#d1fae5;font-family:Menlo,monospace")
        layout.addWidget(self.console, 1)
        self.canvas = PlotCanvas()
        splitter.addWidget(left); splitter.addWidget(self.canvas); splitter.setStretchFactor(1, 1)

    def log(self, message):
        self.console.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")

    def start_stream(self):
        if self.worker and self.worker.isRunning():
            return
        reader = TOOLS_DIR / "monitor_dual_imu.py"
        self.worker = StreamWorker(
            self.host.text().strip() or "mp157",
            self.interface.text().strip() or "en7", reader,
            self.auto_start_m4.isChecked(), self.demo,
            self.rotation, self.icm_accel_bias, self.mpu_accel_bias,
        )
        self.worker.sample_received.connect(self.accept_sample)
        self.worker.log_received.connect(self.log)
        self.worker.status_changed.connect(self.status.setText)
        self.worker.finished.connect(self.stream_finished)
        self.worker.start(); self.start_button.setEnabled(False); self.stop_button.setEnabled(True)

    def stop_stream(self):
        if self.worker:
            self.status.setText("正在停止…"); self.worker.stop()

    def stream_finished(self):
        self.start_button.setEnabled(True); self.stop_button.setEnabled(False)

    def accept_sample(self, sample: Sample):
        icm_accel_mpu = None
        icm_gyro_mpu = None
        if self.calibration_loaded:
            icm_accel_mpu = transform_icm_accel(sample.icm_accel, self.rotation, self.icm_accel_bias)
            icm_gyro_mpu = self.rotation @ np.asarray(sample.icm_gyro)
        spi_ok = sample.spi_errors == self.last_spi_errors
        sync_ok = sample.sync_errors == self.last_sync_errors
        self.last_spi_errors = sample.spi_errors
        self.last_sync_errors = sample.sync_errors
        mpu_corrected = np.asarray(sample.mpu_accel, dtype=float) - self.mpu_accel_bias
        output = self.fusion.update(
            mpu_corrected, icm_accel_mpu, sample.icm_age_ms,
            spi_ok=spi_ok, sync_ok=sync_ok, calibration_ok=self.calibration_loaded)
        sample.fusion_state = output.state
        sample.icm_valid = output.icm_valid
        sample.icm_accel_mpu = tuple(icm_accel_mpu) if icm_accel_mpu is not None else None
        sample.icm_gyro_mpu = tuple(icm_gyro_mpu) if icm_gyro_mpu is not None else None
        sample.accel = output.accel
        sample.gyro = tuple(sample.mpu_gyro)
        self.samples.append(sample)

        if self.last_timestamp is None or sample.timestamp <= self.last_timestamp:
            self.last_timestamp = sample.timestamp
            return
        dt = min(sample.timestamp - self.last_timestamp, 0.1)
        self.last_timestamp = sample.timestamp

        accel = np.asarray(sample.accel if sample.accel is not None
                           else mpu_corrected, dtype=float)
        gyro = np.asarray(sample.gyro, dtype=float)

        if not self.attitude_ready:
            self.attitude_samples.append((accel, gyro))
            if len(self.attitude_samples) >= 100:
                accel_mean = np.mean([item[0] for item in self.attitude_samples], axis=0)
                gyro_mean = np.radians(np.mean([item[1] for item in self.attitude_samples], axis=0))
                magnitude = float(np.linalg.norm(accel_mean))
                self.gravity = magnitude if 9.0 <= magnitude <= 10.5 else GRAVITY
                self.accel_baseline = accel_mean
                self.gyro_bias = gyro_mean
                self.quaternion = quaternion_from_gravity(accel_mean)
                self.gyro_quaternion = self.quaternion.copy()
                self.filtered_accel = accel_mean.copy()
                self.filtered_gyro = np.zeros(3)
                self.attitude_ready = True
                self.log(f"姿态标定完成：重力 {self.gravity:.3f} m/s²，陀螺零偏 "
                         f"{np.array2string(np.degrees(self.gyro_bias), precision=3)} °/s")
            return

        alpha = self.alpha_spin.value()
        self.filtered_accel += alpha * (accel - self.filtered_accel)
        self.filtered_gyro += alpha * (np.radians(gyro) - self.gyro_bias - self.filtered_gyro)
        if sample.accel is not None:
            self.quaternion = update_quaternion(
                self.quaternion, self.filtered_gyro, self.filtered_accel, dt,
                self.kp_spin.value())
        else:
            self.quaternion = integrate_gyro(self.quaternion, self.filtered_gyro, dt)
        self.gyro_quaternion = integrate_gyro(self.gyro_quaternion, self.filtered_gyro, dt)

        if sample.fusion_state == STATE_INVALID:
            return

        if self.gravity_check.isChecked():
            world_accel = rotation_matrix(self.quaternion) @ self.filtered_accel - \
                np.array([0.0, 0.0, self.gravity])
        else:
            world_accel = self.filtered_accel - self.accel_baseline
        gyro_norm = float(np.linalg.norm(self.filtered_gyro))
        static_now = float(np.linalg.norm(world_accel)) < 0.2 and gyro_norm < 0.06
        self.static_counter = self.static_counter + 1 if static_now else 0
        self.is_static = self.static_counter >= 10
        if self.zupt_check.isChecked() and self.is_static:
            self.velocity[:] = 0.0
        else:
            self.velocity = (self.velocity + world_accel * dt) * self.damping.value()
        self.position += self.velocity * dt
        self.positions.append(self.position.copy())

    def refresh_plots(self):
        if self._closing:
            return
        try:
            self.timer.setInterval(self.refresh_ms.value())
            if not self.samples:
                return
            latest = self.samples[-1]
            cutoff = latest.timestamp - self.window_seconds.value()
            visible = [sample for sample in self.samples if sample.timestamp >= cutoff]
            self.canvas.update_data(visible, self.positions, self.gyro_quaternion,
                                    self.raw_check.isChecked())
            rate = 0.0 if len(visible) < 2 else (len(visible)-1) / max(visible[-1].timestamp-visible[0].timestamp, 1e-6)
            state_text = STATE_TEXT.get(latest.fusion_state, latest.fusion_state)
            self.fusion_label.setText(f"融合状态：{state_text}")
            self.fusion_label.setStyleSheet(
                f"font-weight: 600; padding: 2px; color: {STATE_COLOR.get(latest.fusion_state, '#334155')}")
            icm_valid = latest.icm_valid
            self.icm_label.setText(
                f"ICM：{'有效' if icm_valid else '无效'}，age={latest.icm_age_ms:.1f} ms，"
                f"T={latest.icm_temperature:.2f} °C，SPI/SYNC 错误={latest.spi_errors}/{latest.sync_errors}")
            if self.gyro_quaternion is not None:
                gyro_roll, gyro_pitch, gyro_yaw = euler_degrees(self.gyro_quaternion)
                gyro_attitude = f"姿态(陀螺) R/P/Y: {gyro_roll:.1f} / {gyro_pitch:.1f} / {gyro_yaw:.1f} °"
            else:
                gyro_attitude = "姿态(陀螺) R/P/Y: 标定中…"
            if self.quaternion is not None:
                roll, pitch, yaw = euler_degrees(self.quaternion)
                gravity_attitude = f"姿态(重力校正) R/P/Y: {roll:.1f} / {pitch:.1f} / {yaw:.1f} °"
            else:
                gravity_attitude = "姿态(重力校正) R/P/Y: 标定中…"
            self.metrics.setText(
                f"seq: {latest.seq}\n频率: {rate:.1f} Hz\nMPU 温度: {latest.temperature:.2f} °C\n"
                f"I²C/TX 错误: {latest.i2c_errors}/{latest.tx_errors}\n"
                f"{gyro_attitude}\n{gravity_attitude}\n静止: {'是' if self.is_static else '否'}"
            )
        except Exception:
            # An exception escaping a Qt timer slot makes PyQt5 call abort().
            self.timer.stop()
            details = traceback.format_exc()
            self.log("绘图刷新失败，已停止定时器：\n" + details)
            self.status.setText("绘图错误（数据连接已停止）")
            self.stop_stream()

    def clear_data(self):
        self.samples.clear(); self.positions.clear()
        self.attitude_samples.clear(); self.attitude_ready = False
        self.accel_baseline = None; self.gyro_bias = np.zeros(3)
        self.quaternion = None; self.gyro_quaternion = None
        self.filtered_accel = None; self.filtered_gyro = np.zeros(3)
        self.static_counter = 0; self.is_static = False
        self.velocity[:] = 0; self.position[:] = 0; self.last_timestamp = None
        self.last_spi_errors = 0; self.last_sync_errors = 0
        self.fusion = FusionStateMachine()
        self.log("已清空曲线、轨迹、融合状态和积分状态")

    def closeEvent(self, event):
        self._closing = True
        self.timer.stop()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(3000):
                self.worker.terminate()
                self.worker.wait(1000)
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="use simulated 50 Hz dual-IMU data")
    parser.add_argument("--quit-after", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()

    def report_unhandled(exc_type, exc_value, exc_traceback):
        # Prevent PyQt5 from converting an otherwise recoverable slot exception
        # into SIGABRT while still preserving a complete diagnostic traceback.
        traceback.print_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = report_unhandled
    app = QApplication(sys.argv[:1]); window = MainWindow(args.demo); window.show()
    if args.quit_after:
        QTimer.singleShot(int(args.quit_after * 1000), window.close)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
