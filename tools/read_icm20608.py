#!/usr/bin/env python3
"""Read the ATK ICM20608 Linux driver and print SI-unit samples.

The driver exposes /dev/icm20608 and its read() always copies 7 little-endian
int32 values (gyro x/y/z, accel x/y/z, temperature) but returns 0 instead of
the byte count, so libc read() is called directly and the buffer is decoded
regardless of the return value.

Configured full scale (ATK driver): accel +/-16 g, gyro +/-2000 dps.
"""

import argparse
import ctypes
import os
import struct
import sys
import time

DEVICE = "/dev/icm20608"
ACCEL_LSB_PER_G = 2048.0
GYRO_LSB_PER_DPS = 16.4
TEMP_LSB_PER_C = 326.8
TEMP_OFFSET_C = 25.0
STANDARD_GRAVITY = 9.80665

SAMPLE = struct.Struct("<7i")


def open_device(path):
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    fd = os.open(path, os.O_RDONLY)
    buffer = ctypes.create_string_buffer(SAMPLE.size)
    return libc, fd, buffer


def read_sample(libc, fd, buffer):
    libc.read(fd, buffer, SAMPLE.size)
    gyro_x, gyro_y, gyro_z, accel_x, accel_y, accel_z, temperature = SAMPLE.unpack(buffer.raw)
    return {
        "gyro_raw": (gyro_x, gyro_y, gyro_z),
        "accel_raw": (accel_x, accel_y, accel_z),
        "temp_raw": temperature,
        "accel": tuple(value / ACCEL_LSB_PER_G * STANDARD_GRAVITY for value in (accel_x, accel_y, accel_z)),
        "gyro": tuple(value / GYRO_LSB_PER_DPS for value in (gyro_x, gyro_y, gyro_z)),
        "temperature": temperature / TEMP_LSB_PER_C + TEMP_OFFSET_C,
    }


def format_sample(sample, timestamp, raw_only):
    ax, ay, az = sample["accel_raw"]
    gx, gy, gz = sample["gyro_raw"]
    if raw_only:
        return (f"t={timestamp:12.6f} accel_raw=({ax:7d},{ay:7d},{az:7d}) "
                f"gyro_raw=({gx:7d},{gy:7d},{gz:7d}) temp_raw={sample['temp_raw']:6d}")
    ax, ay, az = sample["accel"]
    gx, gy, gz = sample["gyro"]
    magnitude = (ax * ax + ay * ay + az * az) ** 0.5
    return (f"t={timestamp:12.6f} a=({ax:7.3f},{ay:7.3f},{az:7.3f}) m/s2 |a|={magnitude:6.3f} "
            f"w=({gx:8.3f},{gy:8.3f},{gz:8.3f}) dps T={sample['temperature']:6.2f} C")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--count", type=int, default=0, help="number of samples; 0 = until Ctrl+C")
    parser.add_argument("--interval", type=float, default=0.1, help="seconds between samples")
    parser.add_argument("--raw", action="store_true", help="print raw register values only")
    parser.add_argument("--lines", action="store_true", help="one line per sample")
    args = parser.parse_args()

    libc, fd, buffer = open_device(args.device)
    count = 0
    try:
        while args.count == 0 or count < args.count:
            timestamp = time.monotonic()
            sample = read_sample(libc, fd, buffer)
            text = format_sample(sample, timestamp, args.raw)
            if args.lines:
                print(text, flush=True)
            else:
                print(f"\r\033[2K{text}", end="", flush=True)
            count += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if not args.lines:
            print()
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
