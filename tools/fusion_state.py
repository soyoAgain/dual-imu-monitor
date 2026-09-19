#!/usr/bin/env python3
"""Guarded MPU6050 Y-axis fallback fusion state machine (phase 7).

States
------
MPU_PRIMARY    MPU Y trustworthy, ICM unavailable/invalid -> use MPU only
BLENDED        both trustworthy -> weighted Y fusion
ICM_FALLBACK   MPU Y saturated, ICM valid -> use ICM-transformed Y
INVALID        neither usable -> no fused output, pause trajectory integration

Saturation detection uses hysteresis (separate enter/exit thresholds and
consecutive sample counts) so the state does not toggle near the threshold.

Offline validation:
    python3 tools/fusion_state.py --self-test
    python3 tools/fusion_state.py --log <dual_log> --calib <frame_calibration.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

import numpy as np

STATE_MPU_PRIMARY = "MPU_PRIMARY"
STATE_BLENDED = "BLENDED"
STATE_ICM_FALLBACK = "ICM_FALLBACK"
STATE_INVALID = "INVALID"

ACCEL_SATURATION_ENTER_MPS2 = 19.60
ACCEL_SATURATION_EXIT_MPS2 = 19.51
ACCEL_HARD_SATURATION_MPS2 = 19.612  # 32767 raw / 16384 LSB/g * g
ICM_ACCEL_FULL_SCALE_MPS2 = 16.0 * 9.80665


@dataclass
class FusionConfig:
    saturation_enter_mps2: float = ACCEL_SATURATION_ENTER_MPS2
    saturation_exit_mps2: float = ACCEL_SATURATION_EXIT_MPS2
    hard_saturation_mps2: float = ACCEL_HARD_SATURATION_MPS2
    enter_samples: int = 5
    exit_samples: int = 25
    icm_age_limit_ms: float = 30.0
    icm_accel_limit_mps2: float = ICM_ACCEL_FULL_SCALE_MPS2
    sync_limit_ms: float = 50.0
    blend_weight_mpu: float = 0.5


@dataclass
class FusionOutput:
    state: str
    accel: tuple | None
    mpu_weight: float
    icm_valid: bool
    mpu_y_saturated: bool
    reason: str

    @property
    def should_integrate(self) -> bool:
        return self.state != STATE_INVALID


def transform_icm_accel(icm_accel, rotation, bias):
    """Map an ICM20608 accel sample into the MPU frame using the phase-5/6 calibration."""
    return np.asarray(rotation) @ (np.asarray(icm_accel) - np.asarray(bias))


class FusionStateMachine:
    def __init__(self, config=None):
        self.config = config or FusionConfig()
        self.mpu_y_saturated = False
        self.enter_count = 0
        self.exit_count = 0
        self.state = STATE_MPU_PRIMARY

    def update(self, mpu_accel, icm_accel_mpu, icm_age_ms, spi_ok=True,
               sync_ok=True, calibration_ok=True, sync_error_ms=0.0):
        """Feed one frame; returns FusionOutput.

        mpu_accel       : MPU6050 accel in the MPU frame (m/s^2)
        icm_accel_mpu   : ICM20608 accel already transformed to the MPU frame
        icm_age_ms      : age of the ICM sample used for this frame
        spi_ok          : latest ICM SPI read succeeded
        sync_ok         : time alignment within limits
        calibration_ok  : calibration file present and valid
        """
        config = self.config
        mpu = np.asarray(mpu_accel, dtype=float)

        # Hysteretic saturation detection on the MPU Y axis.
        if not self.mpu_y_saturated:
            self.enter_count = self.enter_count + 1 if abs(mpu[1]) >= config.saturation_enter_mps2 else 0
            if self.enter_count >= config.enter_samples:
                self.mpu_y_saturated = True
                self.exit_count = 0
        else:
            self.exit_count = self.exit_count + 1 if abs(mpu[1]) <= config.saturation_exit_mps2 else 0
            if self.exit_count >= config.exit_samples:
                self.mpu_y_saturated = False
                self.enter_count = 0

        icm_valid = bool(spi_ok and calibration_ok and sync_ok and
                         icm_accel_mpu is not None and
                         icm_age_ms is not None and icm_age_ms <= config.icm_age_limit_ms and
                         float(np.linalg.norm(icm_accel_mpu)) <= config.icm_accel_limit_mps2 and
                         sync_error_ms <= config.sync_limit_ms)

        # A frame at or above the hard saturation limit is clipped and must not
        # be trusted, even before the hysteresis flag is confirmed.
        hard_saturated = abs(mpu[1]) >= config.hard_saturation_mps2
        mpu_y_usable = not (self.mpu_y_saturated or hard_saturated)

        if not mpu_y_usable:
            if icm_valid:
                self.state = STATE_ICM_FALLBACK
                accel = (float(mpu[0]), float(icm_accel_mpu[1]), float(mpu[2]))
                return FusionOutput(self.state, accel, 0.0, True, True, "mpu_y_unusable_icm_ok")
            self.state = STATE_INVALID
            return FusionOutput(self.state, None, 0.0, False, True, "mpu_y_unusable_icm_invalid")

        if icm_valid:
            self.state = STATE_BLENDED
            weight = config.blend_weight_mpu
            fused_y = weight * float(mpu[1]) + (1.0 - weight) * float(icm_accel_mpu[1])
            return FusionOutput(self.state, (float(mpu[0]), fused_y, float(mpu[2])),
                                weight, True, False, "both_valid")
        self.state = STATE_MPU_PRIMARY
        return FusionOutput(self.state, (float(mpu[0]), float(mpu[1]), float(mpu[2])),
                            1.0, False, False, "icm_unavailable")


def _synthetic_self_test():
    config = FusionConfig()
    failures = []

    def transitions(states):
        return sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])

    def segments(states):
        result = []
        start = 0
        for i in range(1, len(states) + 1):
            if i == len(states) or states[i] != states[start]:
                result.append((states[start], i - start))
                start = i
        return result

    # 1) oscillation inside the hysteresis band (below enter threshold) -> no toggling
    machine = FusionStateMachine(config)
    states = []
    for index in range(200):
        a_y = 19.50 + 0.04 * np.sin(2.0 * np.pi * index / 10.0)
        states.append(machine.update((0.0, a_y, 9.0), (0.0, a_y, 9.0), icm_age_ms=1.0).state)
    print(f"[self-test] band oscillation transitions: {transitions(states)} (states: {sorted(set(states))})")
    if transitions(states) != 0:
        failures.append("state changed inside the hysteresis band")

    # 2) saturation then recovery: exactly two transitions, no short segments
    machine = FusionStateMachine(config)
    states = []
    for index in range(300):
        a_y = 9.0 if (index < 100 or index >= 200) else 19.6127
        states.append(machine.update((0.0, a_y, 9.0), (0.0, 4.0, 9.0), icm_age_ms=1.0).state)
    short = [segment for segment in segments(states) if segment[1] < 10]
    print(f"[self-test] saturation/recovery transitions: {transitions(states)} "
          f"segments: {segments(states)}")
    if transitions(states) > 2 or short:
        failures.append("saturation transitions are not clean")

    # 3) ICM timeout while MPU Y is saturated -> INVALID, no stale ICM values
    machine = FusionStateMachine(config)
    for _ in range(20):
        machine.update((0.0, 19.6127, 9.0), (0.0, 4.0, 9.0), icm_age_ms=1.0)
    invalid_outputs = [machine.update((0.0, 19.6127, 9.0), (0.0, 4.0, 9.0), icm_age_ms=100.0)
                       for _ in range(50)]
    invalid_ok = all(out.state == STATE_INVALID and out.accel is None for out in invalid_outputs)
    print(f"[self-test] timeout frames marked INVALID without output: {invalid_ok}")
    if not invalid_ok:
        failures.append("ICM timeout did not produce INVALID frames")

    # 4) INVALID must pause trajectory integration
    position = np.zeros(3)
    velocity = np.zeros(3)
    frozen_ok = True
    for out in invalid_outputs:
        before = position.copy()
        if out.should_integrate:
            velocity += np.asarray(out.accel) * 0.02
            position += velocity * 0.02
        if not np.array_equal(position, before):
            frozen_ok = False
            break
    print(f"[self-test] INVALID integration frozen: {frozen_ok}")
    if not frozen_ok:
        failures.append("INVALID state still updated the trajectory")

    # 5) both valid -> BLENDED with the configured weight
    machine = FusionStateMachine(config)
    out = machine.update((0.0, 9.0, 0.0), (0.0, 5.0, 0.0), icm_age_ms=1.0)
    expected = config.blend_weight_mpu * 9.0 + (1.0 - config.blend_weight_mpu) * 5.0
    print(f"[self-test] BLENDED weight check: state={out.state} y={out.accel[1]:.4f} expected={expected:.4f}")
    if out.state != STATE_BLENDED or abs(out.accel[1] - expected) > 1e-9:
        failures.append("BLENDED output mismatch")

    return failures


def _replay(log_path, calib_path):
    import re

    line_re = re.compile(
        r"seq=(?P<seq>\d+) t=(?P<t>[\d.]+) "
        r"mpu_a=(?P<max>[-\d.]+),(?P<may>[-\d.]+),(?P<maz>[-\d.]+) "
        r"mpu_g=[^ ]+ icm_a=(?P<iax>[-\d.]+),(?P<iay>[-\d.]+),(?P<iaz>[-\d.]+) "
        r"icm_g=[^ ]+ age_icm_ms=(?P<age>[-\d.]+) mpu_sat=(?P<sat>\d+)"
    )
    calibration = json.load(open(calib_path, encoding="utf-8"))
    rotation = np.array(calibration["rotation_icm_to_mpu"])
    bias = np.array(calibration.get("icm_accel_bias_mps2", [0.0, 0.0, 0.0]))

    machine = FusionStateMachine()
    counts = {}
    states = []
    frames = 0
    for line in open(log_path, encoding="utf-8", errors="replace"):
        match = line_re.search(line)
        if not match:
            continue
        values = match.groupdict()
        mpu = (float(values["max"]), float(values["may"]), float(values["maz"]))
        icm = (float(values["iax"]), float(values["iay"]), float(values["iaz"]))
        age = float(values["age"])
        out = machine.update(mpu, transform_icm_accel(icm, rotation, bias), age)
        counts[out.state] = counts.get(out.state, 0) + 1
        states.append(out.state)
        frames += 1

    transitions = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])
    print(f"replay log={log_path} frames={frames}")
    print(f"state counts: {counts}")
    print(f"state transitions: {transitions}")

    # jitter: any state segment shorter than 10 frames (0.2 s)
    segments = []
    start = 0
    for i in range(1, len(states) + 1):
        if i == len(states) or states[i] != states[start]:
            segments.append((states[start], i - start))
            start = i
    short = [segment for segment in segments if segment[1] < 10]
    print(f"segments={len(segments)} short_segments(<10 frames)={len(short)}")
    return 0 if not short else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--log", help="dual-IMU log for offline replay")
    parser.add_argument("--calib", help="frame/accel calibration JSON")
    args = parser.parse_args()

    if args.self_test:
        failures = _synthetic_self_test()
        if failures:
            for failure in failures:
                print("FAIL: " + failure)
            return 1
        print("self-test RESULT: PASS")
        return 0

    if args.log and args.calib:
        return _replay(args.log, args.calib)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
