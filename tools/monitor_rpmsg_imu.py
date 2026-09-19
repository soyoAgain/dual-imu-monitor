#!/usr/bin/env python3
"""Continuously display MPU6050 samples received through RPMsg TTY."""

import argparse
import fcntl
import os
import select
import signal
import struct
import sys
import termios
import time


MAGIC = b"IMU1"
PACKET = struct.Struct("<IHHII7hHHI")
ACCEL_LSB_PER_G = 16384.0       # MPU6050 AFS_SEL=0: +/-2 g
GYRO_LSB_PER_DPS = 131.0        # MPU6050 FS_SEL=0: +/-250 deg/s
STANDARD_GRAVITY = 9.80665
LOCK_PATH = "/tmp/rpmsg_imu_reader.lock"


def fnv1a(data: bytes) -> int:
    value = 0x811C9DC5
    for byte in data:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def set_raw(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = (
        (attrs[2] & ~(termios.CSIZE | termios.PARENB))
        | termios.CS8
        | termios.CREAD
        | termios.CLOCAL
    )
    attrs[3] = 0
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def extract_packet(buffer: bytearray):
    while len(buffer) >= PACKET.size:
        offset = buffer.find(MAGIC)
        if offset < 0:
            del buffer[:-3]
            return None
        if offset:
            del buffer[:offset]
        if len(buffer) < PACKET.size:
            return None

        candidate = bytes(buffer[: PACKET.size])
        values = PACKET.unpack(candidate)
        if values[1] != 1 or values[2] != PACKET.size:
            del buffer[0]
            continue
        if fnv1a(candidate[:-4]) != values[-1]:
            del buffer[0]
            continue

        del buffer[: PACKET.size]
        return values
    return None


def format_sample(packet, rate_hz: float, raw_only: bool) -> str:
    sequence, timestamp_ms = packet[3], packet[4]
    ax, ay, az, temperature, gx, gy, gz = packet[5:12]
    i2c_errors, tx_errors = packet[12], packet[13]

    if raw_only:
        return (
            f"seq={sequence:10d}  t={timestamp_ms:10d} ms  {rate_hz:6.2f} Hz  "
            f"accel=({ax:6d},{ay:6d},{az:6d})  "
            f"gyro=({gx:6d},{gy:6d},{gz:6d})  temp_raw={temperature:6d}  "
            f"errors(i2c/tx)={i2c_errors}/{tx_errors}"
        )

    accel = tuple(value / ACCEL_LSB_PER_G * STANDARD_GRAVITY for value in (ax, ay, az))
    gyro = tuple(value / GYRO_LSB_PER_DPS for value in (gx, gy, gz))
    temperature_c = temperature / 340.0 + 36.53
    return (
        f"seq={sequence:10d}  t={timestamp_ms:10d} ms  {rate_hz:6.2f} Hz  "
        f"a=({accel[0]:7.3f},{accel[1]:7.3f},{accel[2]:7.3f}) m/s²  "
        f"ω=({gyro[0]:7.3f},{gyro[1]:7.3f},{gyro[2]:7.3f}) °/s  "
        f"T={temperature_c:6.2f} °C  errors={i2c_errors}/{tx_errors}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/ttyRPMSG0")
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        help="screen refresh interval in seconds (default: 0.2)",
    )
    parser.add_argument("--raw", action="store_true", help="show only raw register values")
    parser.add_argument(
        "--lines",
        action="store_true",
        help="print one line per refresh instead of updating the current line",
    )
    args = parser.parse_args()

    lock = open(LOCK_PATH, "w", encoding="ascii")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ERROR: another RPMsg IMU reader is already running", file=sys.stderr)
        lock.close()
        return 2

    fd = os.open(args.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    set_raw(fd)
    termios.tcflush(fd, termios.TCIFLUSH)
    os.write(fd, b"start")

    buffer = bytearray()
    last_packet = None
    previous_timestamp = None
    sample_rate_hz = 0.0
    next_display = time.monotonic()

    print(f"Monitoring {args.device}; press Ctrl+C to stop.")
    def request_stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGHUP, request_stop)
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                chunk = os.read(fd, 4096)
                if chunk:
                    buffer.extend(chunk)
                while True:
                    packet = extract_packet(buffer)
                    if packet is None:
                        break
                    if previous_timestamp is not None:
                        delta_ms = packet[4] - previous_timestamp
                        if delta_ms > 0:
                            sample_rate_hz = 1000.0 / delta_ms
                    previous_timestamp = packet[4]
                    last_packet = packet

            now = time.monotonic()
            if last_packet is None or now < next_display:
                continue
            next_display = now + max(args.interval, 0.02)
            text = format_sample(last_packet, sample_rate_hz, args.raw)
            if args.lines:
                print(text, flush=True)
            else:
                print(f"\r\033[2K{text}", end="", flush=True)
    except KeyboardInterrupt:
        if not args.lines:
            print()
        print("Stopped.")
    finally:
        try:
            os.write(fd, b"stop")
            time.sleep(0.05)
        except OSError:
            pass
        os.close(fd)
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
