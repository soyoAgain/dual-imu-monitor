#!/usr/bin/env python3
"""Estimate attitude and coil normal from MPU6050 RPMsg samples."""

import argparse
import fcntl
import math
import os
import select
import struct
import sys
import termios
import time


MAGIC = b"IMU1"
PACKET = struct.Struct("<IHHII7hHHI")
ACCEL_SCALE = 16384.0
GYRO_SCALE = 131.0
LOCK_PATH = "/tmp/rpmsg_imu_reader.lock"


def fnv1a(data):
    value = 0x811C9DC5
    for byte in data:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def set_raw(fd):
    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0
    attrs[2] = ((attrs[2] & ~(termios.CSIZE | termios.PARENB)) |
                termios.CS8 | termios.CREAD | termios.CLOCAL)
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


class PacketReader:
    def __init__(self, device):
        self.lock = open(LOCK_PATH, "w", encoding="ascii")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("another RPMsg IMU reader is already running")
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        set_raw(self.fd)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        os.write(self.fd, b"start")
        self.buffer = bytearray()

    def close(self):
        try:
            os.write(self.fd, b"stop")
            time.sleep(0.05)
        except OSError:
            pass
        os.close(self.fd)
        self.lock.close()

    def read(self, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while len(self.buffer) >= PACKET.size:
                offset = self.buffer.find(MAGIC)
                if offset < 0:
                    del self.buffer[:-3]
                    break
                if offset:
                    del self.buffer[:offset]
                if len(self.buffer) < PACKET.size:
                    break
                candidate = bytes(self.buffer[:PACKET.size])
                values = PACKET.unpack(candidate)
                if (values[1] == 1 and values[2] == PACKET.size and
                        fnv1a(candidate[:-4]) == values[-1]):
                    del self.buffer[:PACKET.size]
                    return values
                del self.buffer[0]
            ready, _, _ = select.select([self.fd], [], [], max(0.0, deadline - time.monotonic()))
            if ready:
                chunk = os.read(self.fd, 4096)
                if chunk:
                    self.buffer.extend(chunk)
        raise TimeoutError("two seconds passed without a valid IMU packet")


def normalize(vector):
    length = math.sqrt(sum(value * value for value in vector))
    if length < 1.0e-9:
        raise ValueError("zero-length vector")
    return tuple(value / length for value in vector)


def quaternion_from_gravity(ax, ay, az):
    roll = math.atan2(ay, az)
    pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    return [cr * cp, sr * cp, cr * sp, -sr * sp]


def update_quaternion(q, gyro, accel, dt, kp):
    qw, qx, qy, qz = q
    ax, ay, az = normalize(accel)
    vx = 2.0 * (qx * qz - qw * qy)
    vy = 2.0 * (qw * qx + qy * qz)
    vz = qw * qw - qx * qx - qy * qy + qz * qz
    ex = ay * vz - az * vy
    ey = az * vx - ax * vz
    ez = ax * vy - ay * vx
    gx, gy, gz = (gyro[0] + kp * ex, gyro[1] + kp * ey, gyro[2] + kp * ez)
    qdot = (
        -0.5 * (qx * gx + qy * gy + qz * gz),
         0.5 * (qw * gx + qy * gz - qz * gy),
         0.5 * (qw * gy - qx * gz + qz * gx),
         0.5 * (qw * gz + qx * gy - qy * gx),
    )
    return list(normalize(tuple(q[i] + qdot[i] * dt for i in range(4))))


def euler_degrees(q):
    qw, qx, qy, qz = q
    roll = math.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy))
    pitch_term = max(-1.0, min(1.0, 2.0 * (qw * qy - qz * qx)))
    pitch = math.asin(pitch_term)
    yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))
    return tuple(math.degrees(value) for value in (roll, pitch, yaw))


def coil_normal(q):
    qw, qx, qy, qz = q
    return (
        2.0 * (qx * qz + qw * qy),
        2.0 * (qy * qz - qw * qx),
        1.0 - 2.0 * (qx * qx + qy * qy),
    )


def calibrate(reader, count):
    accel_sum = [0.0, 0.0, 0.0]
    gyro_sum = [0.0, 0.0, 0.0]
    saturated = [0, 0, 0]
    first_sequence = last_sequence = None
    for index in range(count):
        packet = reader.read()
        first_sequence = packet[3] if first_sequence is None else first_sequence
        last_sequence = packet[3]
        accel = packet[5:8]
        gyro = packet[9:12]
        for axis in range(3):
            accel_sum[axis] += accel[axis]
            gyro_sum[axis] += gyro[axis]
            saturated[axis] += abs(accel[axis]) >= 32760
        if (index + 1) % 50 == 0:
            print(f"\rCalibrating while stationary: {index + 1}/{count}", end="", flush=True)
    print()
    mean_accel = tuple(value / count / ACCEL_SCALE for value in accel_sum)
    gyro_bias = tuple(math.radians(value / count / GYRO_SCALE) for value in gyro_sum)
    return mean_accel, gyro_bias, saturated, first_sequence, last_sequence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/ttyRPMSG0")
    parser.add_argument("--calibration-samples", type=int, default=250)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--alpha", type=float, default=0.2, help="low-pass coefficient")
    parser.add_argument("--kp", type=float, default=1.5, help="gravity correction gain")
    parser.add_argument("--allow-saturated", action="store_true",
                        help="diagnostic only: run despite saturated accelerometer")
    parser.add_argument("--lines", action="store_true")
    args = parser.parse_args()
    if not 0.0 < args.alpha <= 1.0:
        parser.error("--alpha must be in (0, 1]")

    reader = PacketReader(args.device)
    try:
        mean_accel, gyro_bias, saturated, seq0, seq1 = calibrate(
            reader, args.calibration_samples)
        print(f"calibration_sequence={seq0}..{seq1}")
        print("mean_accel_g=(%.5f, %.5f, %.5f)" % mean_accel)
        print("gyro_bias_dps=(%.5f, %.5f, %.5f)" % tuple(
            math.degrees(value) for value in gyro_bias))
        print(f"accel_saturation_counts={tuple(saturated)}")
        if any(saturated) and not args.allow_saturated:
            axes = "XYZ"
            affected = ",".join(axes[i] for i, value in enumerate(saturated) if value)
            print(f"FAIL: accelerometer saturation detected on axis {affected}; attitude output blocked")
            return 2

        q = quaternion_from_gravity(*mean_accel)
        filtered_accel = list(mean_accel)
        filtered_gyro = [0.0, 0.0, 0.0]
        previous_timestamp = None
        next_display = time.monotonic()
        print("Attitude running; coil normal assumes sensor +Z is aligned with coil normal.")
        while True:
            packet = reader.read()
            timestamp = packet[4]
            if previous_timestamp is None:
                previous_timestamp = timestamp
                continue
            delta_ms = timestamp - previous_timestamp
            previous_timestamp = timestamp
            if not 1 <= delta_ms <= 100:
                continue
            dt = delta_ms / 1000.0
            accel = tuple(value / ACCEL_SCALE for value in packet[5:8])
            gyro = tuple(math.radians(value / GYRO_SCALE) - gyro_bias[i]
                         for i, value in enumerate(packet[9:12]))
            for axis in range(3):
                filtered_accel[axis] += args.alpha * (accel[axis] - filtered_accel[axis])
                filtered_gyro[axis] += args.alpha * (gyro[axis] - filtered_gyro[axis])
            q = update_quaternion(q, filtered_gyro, filtered_accel, dt, args.kp)
            if time.monotonic() < next_display:
                continue
            next_display = time.monotonic() + max(0.02, args.interval)
            roll, pitch, yaw = euler_degrees(q)
            nx, ny, nz = coil_normal(q)
            text = (f"seq={packet[3]:10d} roll={roll:8.3f}° pitch={pitch:8.3f}° "
                    f"yaw={yaw:8.3f}° normal=({nx: .5f},{ny: .5f},{nz: .5f})")
            if args.lines:
                print(text, flush=True)
            else:
                print(f"\r\033[2K{text}", end="", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
