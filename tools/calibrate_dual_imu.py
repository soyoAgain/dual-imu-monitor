#!/usr/bin/env python3
"""Estimate the time offset between MPU6050 and ICM20608 from dual-IMU logs.

Uses the gyro magnitude, which is invariant to the (unknown) rotation between
the two sensor frames, so the delay can be estimated before the coordinate
calibration of phase 5.

Usage:
    python3 tools/calibrate_dual_imu.py <dual_imu_log> [--window 5.0]

Input: log produced by tools/monitor_dual_imu.py on the board.
Output: overall delay estimate, per-window stability, correlation at zero and
at the estimated delay, and counts of frames that must be marked non-fusable.
"""

import argparse
import math
import re
import sys

import numpy as np

LINE_RE = re.compile(
    r"seq=(?P<seq>\d+) t=(?P<t>[\d.]+) "
    r"mpu_a=[^ ]+ mpu_g=(?P<mgx>[-\d.]+),(?P<mgy>[-\d.]+),(?P<mgz>[-\d.]+) "
    r"icm_a=[^ ]+ icm_g=(?P<igx>[-\d.]+),(?P<igy>[-\d.]+),(?P<igz>[-\d.]+) "
    r"age_icm_ms=(?P<age>[-\d.]+) mpu_sat=(?P<sat>\d+) "
    r"errors=(?P<i2c>\d+)/(?P<tx>\d+)/(?P<spi>\d+)/(?P<sync>\d+)"
)

AGE_LIMIT_MS = 30.0
DELAY_LIMIT_MS = 50.0


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
    mpu = np.array([[float(r["mgx"]), float(r["mgy"]), float(r["mgz"])] for r in rows])
    icm = np.array([[float(r["igx"]), float(r["igy"]), float(r["igz"])] for r in rows])
    age = np.array([float(r["age"]) for r in rows])
    sat = np.array([int(r["sat"]) for r in rows])
    return seq, t, mpu, icm, age, sat


def standardize(values):
    std = float(values.std())
    if std < 1e-9:
        return values - values.mean(), 0.0
    return (values - values.mean()) / std, std


def estimate_delay(t, icm_time, mpu_mag, icm_mag, max_delay, step):
    """Return (tau_s, peak_corr, corr_at_zero, tau_axis, corr_curve)."""
    norm_mpu, _ = standardize(mpu_mag)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logfile")
    parser.add_argument("--window", type=float, default=5.0, help="stability window seconds")
    parser.add_argument("--max-delay", type=float, default=0.5, help="search range seconds")
    parser.add_argument("--step", type=float, default=0.0005, help="search step seconds")
    parser.add_argument("--flags", help="write per-frame 'seq,fusable' flags to this path")
    args = parser.parse_args()

    seq, t, mpu, icm, age, sat = load_frames(args.logfile)
    mpu_mag = np.linalg.norm(mpu, axis=1)
    icm_mag = np.linalg.norm(icm, axis=1)
    icm_time = t + age / 1000.0

    duration = float(t[-1] - t[0])
    tau, peak, corr_zero, _, _ = estimate_delay(t, icm_time, mpu_mag, icm_mag,
                                                args.max_delay, args.step)

    windows = []
    window_len = args.window
    start = t[0]
    while start + window_len <= t[-1] + 1e-9:
        mask = (t >= start) & (t < start + window_len)
        if mask.sum() > 50:
            window_tau, window_peak, _, _, _ = estimate_delay(
                t[mask], icm_time[mask], mpu_mag[mask], icm_mag[mask],
                args.max_delay, args.step)
            windows.append((start, window_tau, window_peak))
        start += window_len

    tau_values = [item[1] for item in windows]
    spread = (max(tau_values) - min(tau_values)) if tau_values else 0.0
    age_bad = int((age > AGE_LIMIT_MS).sum())
    delay_bad = int(abs(tau) * 1000.0 > DELAY_LIMIT_MS)
    motion = float(mpu_mag.max())

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
    print(f"frames marked non-fusable (|tau|>{DELAY_LIMIT_MS:.0f} ms): {delay_bad}")
    print(f"motion present: {'yes' if motion > 20.0 else 'NO (max |w| %.2f dps)' % motion}")

    fusable = (age <= AGE_LIMIT_MS) & (abs(tau) * 1000.0 <= DELAY_LIMIT_MS)
    print(f"fusable frames: {int(fusable.sum())}/{len(fusable)}")
    if args.flags:
        with open(args.flags, "w", encoding="ascii") as handle:
            for frame_seq, ok in zip(seq, fusable):
                handle.write("%d,%d\n" % (frame_seq, int(ok)))
        print(f"per-frame flags written to {args.flags}")

    passed = (motion > 20.0 and windows and spread * 1000.0 < 10.0 and
              peak > corr_zero and age_bad == 0 and delay_bad == 0)
    print("RESULT: " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
