#!/usr/bin/env python3
"""Probe MPU6050 addresses on Linux I2C character devices."""

import fcntl
import glob
import os
import sys


I2C_SLAVE = 0x0703
WHO_AM_I = 0x75
ADDRESSES = (0x68, 0x69)


def read_register(device: str, address: int, register: int) -> int:
    fd = os.open(device, os.O_RDWR)
    try:
        fcntl.ioctl(fd, I2C_SLAVE, address)
        os.write(fd, bytes((register,)))
        data = os.read(fd, 1)
        if len(data) != 1:
            raise OSError(f"short read: expected 1 byte, got {len(data)}")
        return data[0]
    finally:
        os.close(fd)


def main() -> int:
    devices = sorted(glob.glob("/dev/i2c-*"))
    if not devices:
        print("ERROR: no /dev/i2c-* devices found", file=sys.stderr)
        return 2

    print("Available I2C devices:", " ".join(devices))
    found = []

    for device in devices:
        for address in ADDRESSES:
            try:
                value = read_register(device, address, WHO_AM_I)
            except OSError as exc:
                print(
                    f"MISS {device} address=0x{address:02x}: "
                    f"{exc.strerror or exc}"
                )
                continue

            valid = value in ADDRESSES
            state = "FOUND" if valid else "UNEXPECTED"
            print(
                f"{state} {device} address=0x{address:02x} "
                f"WHO_AM_I=0x{value:02x}"
            )
            if valid:
                found.append((device, address, value))

    if not found:
        print("RESULT: MPU6050 not detected")
        return 1

    for device, address, value in found:
        print(
            f"RESULT: MPU6050 detected on {device}, "
            f"address=0x{address:02x}, WHO_AM_I=0x{value:02x}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
