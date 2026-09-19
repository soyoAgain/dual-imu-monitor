#!/usr/bin/env python3
"""Read and validate fixed-size MPU6050 packets from an RPMsg TTY."""

import argparse
import fcntl
import os
import select
import struct
import termios
import time


MAGIC = b"IMU1"
PACKET = struct.Struct("<IHHII7hHHI")
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
    attrs[2] = (attrs[2] & ~(termios.CSIZE | termios.PARENB)) | termios.CS8 | termios.CREAD
    attrs[3] = 0
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/ttyRPMSG0")
    parser.add_argument("--count", type=int, default=250)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    lock = open(LOCK_PATH, "w", encoding="ascii")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("FAIL: another RPMsg IMU reader is already running")
        lock.close()
        return 2

    fd = os.open(args.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    set_raw(fd)
    # Linux 5.4 assigns the tty endpoint address on the first host-to-M4 frame.
    os.write(fd, b"start")
    buffer = bytearray()
    packets = []
    bad_checksums = 0
    deadline = time.monotonic() + args.timeout

    try:
        while len(packets) < args.count and time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                continue
            buffer.extend(chunk)

            while len(buffer) >= PACKET.size:
                offset = buffer.find(MAGIC)
                if offset < 0:
                    del buffer[:-3]
                    break
                if offset:
                    del buffer[:offset]
                if len(buffer) < PACKET.size:
                    break
                candidate = bytes(buffer[:PACKET.size])
                values = PACKET.unpack(candidate)
                if values[1] != 1 or values[2] != PACKET.size:
                    del buffer[0]
                    continue
                if fnv1a(candidate[:-4]) != values[-1]:
                    bad_checksums += 1
                    del buffer[0]
                    continue
                packets.append(values)
                del buffer[:PACKET.size]
    finally:
        try:
            os.write(fd, b"stop")
            time.sleep(0.05)
        except OSError:
            pass
        os.close(fd)
        lock.close()

    if len(packets) < 2:
        print(f"FAIL: only received {len(packets)} valid packet(s); bad_checksums={bad_checksums}")
        return 1

    sequences = [packet[3] for packet in packets]
    timestamps = [packet[4] for packet in packets]
    sequence_steps = [b - a for a, b in zip(sequences, sequences[1:])]
    time_steps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    dropped = sum(max(0, step - 1) for step in sequence_steps)
    mean_period = sum(time_steps) / len(time_steps)
    last = packets[-1]
    passed = bad_checksums == 0 and dropped == 0 and all(step == 20 for step in time_steps)

    print(f"device={args.device}")
    print(f"packets={len(packets)} sequence={sequences[0]}..{sequences[-1]}")
    print(f"mean_period_ms={mean_period:.3f} rate_hz={1000.0 / mean_period:.3f}")
    print(f"min_period_ms={min(time_steps)} max_period_ms={max(time_steps)}")
    print(f"dropped={dropped} bad_checksums={bad_checksums}")
    print(f"i2c_errors={last[-3]} tx_errors={last[-2]}")
    print(
        "last_sample="
        f"ax={last[5]} ay={last[6]} az={last[7]} temp={last[8]} "
        f"gx={last[9]} gy={last[10]} gz={last[11]}"
    )
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
