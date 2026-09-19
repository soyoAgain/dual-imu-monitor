#!/usr/bin/env python3
"""Dual-IMU calibration: time offset (phase 4) and frame rotation (phase 5).

Time alignment uses the gyro magnitude, which is invariant to the (unknown)
rotation between the two sensor frames. Frame calibration then solves the
Wahba/Kabsch problem on the time-aligned, bias-corrected gyro vectors:

    R = argmin_{R in SO(3)} sum_k || (w_mpu,k - b_mpu) - R (w_icm,k - b_icm) ||^2

Usage:
    python3 tools/calibrate_dual_imu.py <rotation_log> [--static static.log]
        [--validate rotation2.log] [--frame-json out.json] [--flags flags.csv]

Input logs are produced by tools/monitor_dual_imu.py on the board.
"""

import argparse
import json
import re
import sys

import numpy as np
from scipy.optimize import least_squares

LINE_RE = re.compile(
    r"seq=(?P<seq>\d+) t=(?P<t>[\d.]+) "
    r"mpu_a=(?P<max>[-\d.]+),(?P<may>[-\d.]+),(?P<maz>[-\d.]+) "
    r"mpu_g=(?P<mgx>[-\d.]+),(?P<mgy>[-\d.]+),(?P<mgz>[-\d.]+) "
    r"icm_a=(?P<iax>[-\d.]+),(?P<iay>[-\d.]+),(?P<iaz>[-\d.]+) "
    r"icm_g=(?P<igx>[-\d.]+),(?P<igy>[-\d.]+),(?P<igz>[-\d.]+) "
    r"age_icm_ms=(?P<age>[-\d.]+) mpu_sat=(?P<sat>\d+) "
    r"errors=(?P<i2c>\d+)/(?P<tx>\d+)/(?P<spi>\d+)/(?P<sync>\d+)"
)

AGE_LIMIT_MS = 30.0
DELAY_LIMIT_MS = 50.0
MIN_VALIDATION_CORRELATION = 0.9
STANDARD_GRAVITY = 9.80665


def load_frames(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = LINE_RE.search(line)
            if match:
                rows.append(match.groupdict())
    if not rows:
        raise SystemExit(f"no valid frames found in {path}")
    seq = np.array([int(r["seq"]) for r in rows])
    t = np.array([float(r["t"]) for r in rows])
    mpu_gyro = np.array([[float(r["mgx"]), float(r["mgy"]), float(r["mgz"])] for r in rows])
    icm_gyro = np.array([[float(r["igx"]), float(r["igy"]), float(r["igz"])] for r in rows])
    mpu_accel = np.array([[float(r["max"]), float(r["may"]), float(r["maz"])] for r in rows])
    icm_accel = np.array([[float(r["iax"]), float(r["iay"]), float(r["iaz"])] for r in rows])
    age = np.array([float(r["age"]) for r in rows])
    sat = np.array([int(r["sat"]) for r in rows])
    return seq, t, mpu_gyro, icm_gyro, mpu_accel, icm_accel, age, sat


def standardize(values):
    std = float(values.std())
    if std < 1e-9:
        return values - values.mean(), 0.0
    return (values - values.mean()) / std, std


def estimate_delay(t, icm_time, mpu_mag, icm_mag, max_delay, step):
    """Return (tau_s, peak_corr, corr_at_zero, tau_axis, corr_curve)."""
    taus = np.arange(-max_delay, max_delay + step / 2.0, step)
    corr = np.empty_like(taus)
    for index, tau in enumerate(taus):
        shifted = np.interp(t - tau, icm_time, icm_mag, left=np.nan, right=np.nan)
        mask = ~np.isnan(shifted)
        if mask.sum() < 10:
            corr[index] = -1.0
            continue
        a, _ = standardize(mpu_mag[mask])
        b, _ = standardize(shifted[mask])
        corr[index] = float((a * b).mean())
    best = int(np.argmax(corr))
    corr_zero = corr[int(np.argmin(np.abs(taus)))]
    return float(taus[best]), float(corr[best]), float(corr_zero), taus, corr


def gyro_bias_from_static(path):
    _, _, mpu, icm, _, _, _, _ = load_frames(path)
    return mpu.mean(axis=0), icm.mean(axis=0)


def align_icm(t, icm_time, icm, tau):
    aligned = np.empty_like(icm)
    for axis in range(3):
        aligned[:, axis] = np.interp(t - tau, icm_time, icm[:, axis])
    return aligned


def solve_rotation(icm_centered, mpu_centered):
    """Kabsch/Wahba solution mapping ICM vectors to the MPU frame."""
    covariance = icm_centered.T @ mpu_centered
    u, singular, vt = np.linalg.svd(covariance)
    v = vt.T
    d = np.sign(np.linalg.det(v @ u.T))
    rotation = v @ np.diag([1.0, 1.0, d]) @ u.T
    return rotation, singular


def axis_mapping(rotation):
    lines = []
    for row, name in enumerate("XYZ"):
        column = int(np.argmax(np.abs(rotation[row])))
        lines.append(f"MPU {name} <- ICM {'XYZ'[column]} ({rotation[row, column]:+.4f})")
    return lines


def segment_static_poses(icm_accel, mpu_gyro, icm_gyro, min_samples=50,
                         gyro_limit=15.0, min_direction_change_deg=10.0):
    """Split a multi-pose log: static runs by gyro, merged by gravity direction."""
    gyro_mag = np.maximum(np.linalg.norm(mpu_gyro, axis=1), np.linalg.norm(icm_gyro, axis=1))
    runs = []
    start = None
    for index in range(len(gyro_mag)):
        if gyro_mag[index] < gyro_limit and start is None:
            start = index
        elif gyro_mag[index] >= gyro_limit and start is not None:
            if index - start >= min_samples:
                runs.append((start, index))
            start = None
    if start is not None and len(gyro_mag) - start >= min_samples:
        runs.append((start, len(gyro_mag)))

    poses = []
    for begin, end in runs:
        vectors = icm_accel[begin:end]
        mean_vector = vectors.mean(axis=0)
        norm = float(np.linalg.norm(mean_vector))
        if norm < 1e-6:
            continue
        direction = mean_vector / norm
        if poses:
            previous = poses[-1][2]
            angle = np.degrees(np.arccos(np.clip(float(np.dot(previous, direction)), -1.0, 1.0)))
            if angle < min_direction_change_deg:
                continue
        poses.append((begin, end, direction))
    return poses


def fit_accel_gravity(pose_icm, rotation, gravity=STANDARD_GRAVITY, regularization=0.01):
    """Fit the ICM accel bias against the gravity magnitude on static poses.

    The MPU6050 accelerometer is itself biased (constant X/Z offset against the
    ICM-transformed data), so it is not used as the calibration reference.
    A weak regularization keeps the bias at the minimum-norm solution.
    """
    def residuals(params):
        bias = params[0:3]
        out = []
        for a_icm in pose_icm:
            predicted = rotation @ (a_icm - bias)
            out.append(np.linalg.norm(predicted) - gravity)
        out.extend(np.sqrt(regularization) * bias)
        return np.array(out)

    solution = least_squares(residuals, np.zeros(3), method="lm")
    return solution.x, solution


def icm_time_of(t, age):
    return t + age / 1000.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logfile", help="rotation log used for calibration")
    parser.add_argument("--window", type=float, default=5.0, help="stability window seconds")
    parser.add_argument("--max-delay", type=float, default=0.5, help="search range seconds")
    parser.add_argument("--step", type=float, default=0.0005, help="search step seconds")
    parser.add_argument("--flags", help="write per-frame 'seq,fusable' flags to this path")
    parser.add_argument("--static", help="static dual-IMU log for gyro biases")
    parser.add_argument("--validate", help="independent rotation log for validation")
    parser.add_argument("--frame-json", help="save calibration results as JSON")
    parser.add_argument("--accel-static", help="multi-pose static log for accel calibration")
    parser.add_argument("--accel-json", help="save accel calibration as JSON")
    args = parser.parse_args()

    seq, t, mpu, icm, mpu_accel, icm_accel, age, sat = load_frames(args.logfile)
    mpu_mag = np.linalg.norm(mpu, axis=1)
    icm_mag = np.linalg.norm(icm, axis=1)
    icm_time = icm_time_of(t, age)

    duration = float(t[-1] - t[0])
    tau, peak, corr_zero, _, _ = estimate_delay(t, icm_time, mpu_mag, icm_mag,
                                                args.max_delay, args.step)

    windows = []
    start = t[0]
    while start + args.window <= t[-1] + 1e-9:
        mask = (t >= start) & (t < start + args.window)
        if mask.sum() > 50:
            window_tau, window_peak, _, _, _ = estimate_delay(
                t[mask], icm_time[mask], mpu_mag[mask], icm_mag[mask],
                args.max_delay, args.step)
            windows.append((start, window_tau, window_peak))
        start += args.window

    tau_values = [item[1] for item in windows]
    spread = (max(tau_values) - min(tau_values)) if tau_values else 0.0
    age_bad = int((age > AGE_LIMIT_MS).sum())
    motion = float(mpu_mag.max())
    fusable = (age <= AGE_LIMIT_MS) & (abs(tau) * 1000.0 <= DELAY_LIMIT_MS)

    print(f"log={args.logfile}")
    print(f"frames={len(t)} duration={duration:.2f} s rate={len(t)/max(duration,1e-9):.2f} Hz")
    print(f"gyro magnitude: mpu max={mpu_mag.max():.2f} dps, icm max={icm_mag.max():.2f} dps")
    print(f"estimated delay tau = {tau*1000.0:+.2f} ms (peak corr {peak:.4f})")
    print(f"correlation at tau=0: {corr_zero:.4f} -> at tau={tau*1000.0:+.2f} ms: {peak:.4f}")
    if windows:
        text = ", ".join(f"{item[1]*1000.0:+.2f}" for item in windows)
        print(f"per-window tau ({args.window:.1f} s): [{text}] ms, spread {spread*1000.0:.2f} ms")
    else:
        print("per-window tau: not enough data")
    print(f"frames with age>{AGE_LIMIT_MS:.0f} ms: {age_bad}")
    print(f"frames marked non-fusable (|tau|>{DELAY_LIMIT_MS:.0f} ms): {int(abs(tau)*1000.0 > DELAY_LIMIT_MS)}")
    print(f"motion present: {'yes' if motion > 20.0 else 'NO (max |w| %.2f dps)' % motion}")
    print(f"fusable frames: {int(fusable.sum())}/{len(fusable)}")
    if args.flags:
        with open(args.flags, "w", encoding="ascii") as handle:
            for frame_seq, ok in zip(seq, fusable):
                handle.write("%d,%d\n" % (frame_seq, int(ok)))
        print(f"per-frame flags written to {args.flags}")

    time_pass = (motion > 20.0 and windows and spread * 1000.0 < 10.0 and
                 peak > corr_zero and age_bad == 0)

    frame_pass = None
    rotation = None
    if args.static:
        bias_mpu, bias_icm = gyro_bias_from_static(args.static)
        aligned = align_icm(t, icm_time, icm, tau)
        m = mpu - bias_mpu
        i = aligned - bias_icm
        rotation, singular = solve_rotation(i, m)
        residual = i @ rotation.T - m
        rmse = np.sqrt((residual ** 2).mean(axis=0))
        orthogonality = float(np.linalg.norm(rotation.T @ rotation - np.eye(3), ord="fro"))
        determinant = float(np.linalg.det(rotation))
        condition = float(np.linalg.cond(i.T @ i))

        print()
        print("== frame calibration ==")
        print(f"gyro bias mpu (dps): {np.array2string(bias_mpu, precision=4)}")
        print(f"gyro bias icm (dps): {np.array2string(bias_icm, precision=4)}")
        print("rotation icm -> mpu:")
        for row in rotation:
            print("  [" + ", ".join(f"{value:+.5f}" for value in row) + "]")
        for line in axis_mapping(rotation):
            print("  " + line)
        print(f"calibration RMSE (dps): {np.array2string(rmse, precision=3)}")
        print(f"orthogonality error={orthogonality:.3e} determinant={determinant:.6f}")
        print(f"rotation-data covariance condition number={condition:.2f}")

        validation = None
        if args.validate:
            _, t_v, mpu_v, icm_v, _, _, age_v, _ = load_frames(args.validate)
            icm_time_v = icm_time_of(t_v, age_v)
            mpu_mag_v = np.linalg.norm(mpu_v, axis=1)
            icm_mag_v = np.linalg.norm(icm_v, axis=1)
            tau_v, peak_v, zero_v, _, _ = estimate_delay(t_v, icm_time_v, mpu_mag_v,
                                                         icm_mag_v, args.max_delay, args.step)
            aligned_v = align_icm(t_v, icm_time_v, icm_v, tau_v)
            m_v = mpu_v - bias_mpu
            i_v = aligned_v - bias_icm
            predicted = i_v @ rotation.T
            correlations = [float(np.corrcoef(predicted[:, axis], m_v[:, axis])[0, 1])
                            for axis in range(3)]
            rmse_v = np.sqrt(((predicted - m_v) ** 2).mean(axis=0))
            validation = {"time_offset_ms": tau_v * 1000.0, "correlation": correlations,
                          "rmse_dps": rmse_v.tolist()}
            print("== validation on independent log ==")
            print(f"log={args.validate} frames={len(t_v)}")
            print(f"validation tau = {tau_v*1000.0:+.2f} ms (peak corr {peak_v:.4f}, at 0 {zero_v:.4f})")
            print(f"per-axis correlation: {np.array2string(np.array(correlations), precision=4)}")
            print(f"per-axis RMSE (dps): {np.array2string(rmse_v, precision=3)}")

        frame_pass = (determinant > 0.999 and orthogonality < 1e-6 and
                      (validation is None or min(validation["correlation"]) > MIN_VALIDATION_CORRELATION))

        if args.frame_json:
            payload = {
                "version": 1,
                "time_offset_ms": tau * 1000.0,
                "gyro_bias_mpu_dps": bias_mpu.tolist(),
                "gyro_bias_icm_dps": bias_icm.tolist(),
                "rotation_icm_to_mpu": rotation.tolist(),
                "metrics": {
                    "calibration_rmse_dps": rmse.tolist(),
                    "orthogonality_error": orthogonality,
                    "determinant": determinant,
                    "condition_number": condition,
                },
                "validation": validation,
            }
            with open(args.frame_json, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            print(f"calibration JSON written to {args.frame_json}")

    accel_pass = None
    if args.accel_static:
        if rotation is None:
            raise SystemExit("--accel-static requires --static (gyro frame calibration)")
        _, _, _, _, mpu_a, icm_a, _, _ = load_frames(args.accel_static)
        _, _, mpu_g, icm_g, _, _, _, _ = load_frames(args.accel_static)
        pose_ranges = segment_static_poses(icm_a, mpu_g, icm_g)
        if len(pose_ranges) < 4:
            raise SystemExit(f"only {len(pose_ranges)} distinct static poses detected; need at least 4")
        static_idx = np.concatenate([np.arange(begin, end) for begin, end, _ in pose_ranges])
        pose_icm = np.array([icm_a[begin:end].mean(axis=0) for begin, end, _ in pose_ranges])
        pose_mpu = np.array([mpu_a[begin:end].mean(axis=0) for begin, end, _ in pose_ranges])

        bias_a, solution = fit_accel_gravity(pose_icm, rotation)
        predicted_poses = np.array([rotation @ (a - bias_a) for a in pose_icm])
        magnitudes = np.linalg.norm(predicted_poses, axis=1)
        gravity_rmse = float(np.sqrt(((magnitudes - STANDARD_GRAVITY) ** 2).mean()))

        static_pred = np.array([rotation @ (a - bias_a) for a in icm_a[static_idx]])
        static_mpu = mpu_a[static_idx]
        corr_x = float(np.corrcoef(predicted_poses[:, 0], pose_mpu[:, 0])[0, 1])
        corr_z = float(np.corrcoef(predicted_poses[:, 2], pose_mpu[:, 2])[0, 1])
        slope_x = float(np.polyfit(pose_mpu[:, 0], predicted_poses[:, 0], 1)[0])
        slope_z = float(np.polyfit(pose_mpu[:, 2], predicted_poses[:, 2], 1)[0])
        residual = static_pred - static_mpu
        offset = residual.mean(axis=0)
        # MPU accelerometer bias relative to the gravity-consistent ICM reference
        # (Y is unknown because the MPU Y axis is saturated).
        mpu_bias = np.array([-offset[0], 0.0, -offset[2]])
        detrended_rmse = np.sqrt(((residual - offset) ** 2).mean(axis=0))
        y_range = (float(static_pred[:, 1].min()), float(static_pred[:, 1].max()))

        print()
        print("== accel calibration ==")
        print(f"poses detected: {len(pose_ranges)} (samples: {[end - begin for begin, end, _ in pose_ranges]})")
        print(f"icm accel bias (m/s^2): {np.array2string(bias_a, precision=4)}")
        print(f"per-pose |a_pred| vs g: {np.array2string(magnitudes, precision=3)}")
        print(f"gravity RMSE: {gravity_rmse:.4f} m/s^2")
        print(f"pose-mean correlation with MPU: x={corr_x:.5f} z={corr_z:.5f}; "
              f"slope x={slope_x:.4f} z={slope_z:.4f}")
        print(f"static mean offset (pred - mpu): {np.array2string(offset, precision=3)} m/s^2 "
              "(MPU accelerometer bias)")
        print(f"static detrended RMSE: {np.array2string(detrended_rmse, precision=3)} m/s^2")
        print(f"reconstructed Y range (static): {y_range[0]:.3f} .. {y_range[1]:.3f} m/s^2")
        accel_pass = (abs(float(magnitudes.mean()) - STANDARD_GRAVITY) < 0.1 and
                      corr_x > 0.99 and corr_z > 0.99 and
                      abs(slope_x - 1.0) < 0.1 and abs(slope_z - 1.0) < 0.1 and
                      (y_range[1] - y_range[0]) > 1.0)

        if args.accel_json:
            payload = {
                "version": 1,
                "gravity": STANDARD_GRAVITY,
                "icm_accel_bias_mps2": bias_a.tolist(),
                "icm_accel_scale": [1.0, 1.0, 1.0],
                "rotation_icm_to_mpu": rotation.tolist(),
                "metrics": {
                    "poses": len(pose_ranges),
                    "pose_samples": [end - begin for begin, end, _ in pose_ranges],
                    "gravity_rmse_mps2": gravity_rmse,
                    "pose_correlation_x": corr_x,
                    "pose_correlation_z": corr_z,
                    "pose_slope_x": slope_x,
                    "pose_slope_z": slope_z,
                    "mpu_accel_bias_mps2": mpu_bias.tolist(),
                    "raw_offset_mps2": offset.tolist(),
                    "detrended_rmse_mps2": detrended_rmse.tolist(),
                    "reconstructed_y_range_mps2": list(y_range),
                },
            }
            with open(args.accel_json, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            print(f"accel calibration JSON written to {args.accel_json}")

    passed = time_pass and (frame_pass if frame_pass is not None else True) and \
        (accel_pass if accel_pass is not None else True)
    print("RESULT: " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
