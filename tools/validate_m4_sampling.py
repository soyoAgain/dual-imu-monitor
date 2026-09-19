#!/usr/bin/env python3
"""Validate M4 sample sequence and timestamp spacing through shared SRAM."""

import mmap
import statistics
import struct
import time


MCU_SRAM_BASE = 0x10020000
STATUS_OFFSET = 0x100
STATUS_FORMAT = "<11I7iI"


def read_consistent(region):
    for _ in range(20):
        first = struct.unpack_from(STATUS_FORMAT, region, STATUS_OFFSET)
        second = struct.unpack_from(STATUS_FORMAT, region, STATUS_OFFSET)
        if first[2] == second[2]:
            return second
    raise RuntimeError("M4 status changed too quickly to obtain a stable frame")


def main() -> int:
    observations = []
    with open("/dev/mem", "rb", buffering=0) as memory:
        region = mmap.mmap(
            memory.fileno(),
            mmap.PAGESIZE,
            flags=mmap.MAP_SHARED,
            prot=mmap.PROT_READ,
            offset=MCU_SRAM_BASE,
        )
        try:
            for _ in range(21):
                fields = read_consistent(region)
                observations.append(fields)
                time.sleep(0.1)
        finally:
            region.close()

    first = observations[0]
    last = observations[-1]
    sequence_delta = last[2] - first[2]
    timestamp_delta = last[7] - first[7]
    periods = []
    for older, newer in zip(observations, observations[1:]):
        sequence_step = newer[2] - older[2]
        if sequence_step > 0:
            periods.append((newer[7] - older[7]) / sequence_step)

    mean_period = statistics.fmean(periods)
    rate_hz = 1000.0 / mean_period
    print(f"sequence: {first[2]} -> {last[2]} (delta={sequence_delta})")
    print(f"timestamp_ms: {first[7]} -> {last[7]} (delta={timestamp_delta})")
    print(f"mean_period_ms={mean_period:.6f} rate_hz={rate_hz:.3f}")
    print(
        f"configured_period_ms={last[8]} overruns={last[9]} "
        f"i2c_errors={last[10]} last_error={last[18]}"
    )
    print(
        f"accel=({last[11]},{last[12]},{last[13]}) "
        f"gyro=({last[15]},{last[16]},{last[17]})"
    )

    passed = (
        first[0] == 0x4D505536
        and last[1] == 2
        and sequence_delta > 0
        and abs(mean_period - last[8]) < 0.05
        and last[9] == 0
        and last[10] == 0
        and last[18] == 0
    )
    print("RESULT: PASS" if passed else "RESULT: FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
