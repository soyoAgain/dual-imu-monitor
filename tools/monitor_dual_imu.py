#!/usr/bin/env python3
"""Collect synchronized MPU6050 (RPMsg) and ICM20608 (/dev/icm20608) samples.

Runs on the MP157 board and prints one line per MPU6050 frame, so a single SSH
stream carries both sensors. All timestamps use Linux CLOCK_MONOTONIC:

  seq=<n> t=<seconds> mpu_a=<m/s2> mpu_g=<dps> icm_a=<m/s2> icm_g=<dps>
  age_icm_ms=<ms> mpu_sat=<mask> errors=<m4_i2c>/<m4_tx>/<spi>/<sync> m4_ms=<n>

- t        : MPU6050 sample time mapped to Linux monotonic seconds
- mpu_sat  : bit0/bit1/bit2 = MPU6050 accel X/Y/Z raw magnitude >= 32760
- age_icm_ms: ICM20608 sample time minus this frame's mapped MPU sample time
- errors   : cumulative M4 i2c errors, M4 tx errors, ICM SPI read failures,
             and timestamp-mapping jumps (> SYNC_JUMP_MS)

The ATK icm20608 read() always copies 7 little-endian int32 values but returns
0, so libc read() is called directly with a fixed 28-byte buffer.
"""

import argparse
import ctypes
import fcntl
import os
import select
import struct
import sys
import termios
import time

MPU_DEVICE = "/dev/ttyRPMSG0"
ICM_DEVICE = "/dev/icm20608"
MAGIC = b"IMU1"
PACKET = struct.Struct("<IHHII7hHHI")
ACCEL_LSB_PER_G = 16384.0
GYRO_LSB_PER_DPS = 131.0
STANDARD_GRAVITY = 9.80665
ICM_ACCEL_LSB_PER_G = 2048.0
ICM_GYRO_LSB_PER_DPS = 16.4
ICM_SAMPLE = struct.Struct("<7i")
LOCK_PATH = "/tmp/rpmsg_imu_reader.lock"
OFFSET_ALPHA = 0.05
SYNC_JUMP_MS = 20.0
SATURATION_RAW = 32760


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


class MpuReader:
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


class IcmReader:
    def __init__(self, device):
        self.libc = ctypes.CDLL("libc.so.6", use_errno=True)
        self.fd = os.open(device, os.O_RDONLY)
        self.buffer = ctypes.create_string_buffer(ICM_SAMPLE.size)

    def close(self):
        os.close(self.fd)

    def read(self):
        """Return (values, valid); values = (gyro_xyz, accel_xyz, temp_raw)."""
        self.libc.read(self.fd, self.buffer, ICM_SAMPLE.size)
        gyro_x, gyro_y, gyro_z, accel_x, accel_y, accel_z, temperature = ICM_SAMPLE.unpack(self.buffer.raw)
        values = ((gyro_x, gyro_y, gyro_z), (accel_x, accel_y, accel_z), temperature)
        valid = any(value != 0 for value in (gyro_x, gyro_y, gyro_z, accel_x, accel_y, accel_z, temperature))
        return values, valid


def format_values(values):
    return ",".join("%.4f" % value for value in values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mpu-device", default=MPU_DEVICE)
    parser.add_argument("--icm-device", default=ICM_DEVICE)
    parser.add_argument("--count", type=int, default=0, help="number of MPU frames; 0 = until Ctrl+C")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    mpu = MpuReader(args.mpu_device)
    icm = IcmReader(args.icm_device)

    offset = None
    icm_values = None
    icm_time = None
    spi_errors = 0
    sync_errors = 0
    frames = 0

    try:
        while args.count == 0 or frames < args.count:
            packet = mpu.read(args.timeout)
            now = time.monotonic()

            values, valid = icm.read()
            if valid:
                icm_values = values
                icm_time = time.monotonic()
            else:
                spi_errors += 1

            m4_ms = packet[4]
            offset_estimate = now - m4_ms / 1000.0
            if offset is None:
                offset = offset_estimate
            else:
                if abs(offset_estimate - offset) * 1000.0 > SYNC_JUMP_MS:
                    sync_errors += 1
                offset += OFFSET_ALPHA * (offset_estimate - offset)
            mapped_time = m4_ms / 1000.0 + offset

            accel = tuple(value / ACCEL_LSB_PER_G * STANDARD_GRAVITY for value in packet[5:8])
            gyro = tuple(value / GYRO_LSB_PER_DPS for value in packet[9:12])
            saturation = 0
            for axis, raw in enumerate(packet[5:8]):
                if abs(raw) >= SATURATION_RAW:
                    saturation |= 1 << axis

            if icm_values is None:
                icm_accel = (0.0, 0.0, 0.0)
                icm_gyro = (0.0, 0.0, 0.0)
                age_ms = -1.0
            else:
                icm_gyro = tuple(value / ICM_GYRO_LSB_PER_DPS for value in icm_values[0])
                icm_accel = tuple(value / ICM_ACCEL_LSB_PER_G * STANDARD_GRAVITY for value in icm_values[1])
                # ICM sample time minus this frame's mapped MPU sample time.
                age_ms = (icm_time - mapped_time) * 1000.0

            print(
                "seq=%d t=%.6f mpu_a=%s mpu_g=%s icm_a=%s icm_g=%s age_icm_ms=%.2f "
                "mpu_sat=%d errors=%d/%d/%d/%d m4_ms=%d" % (
                    packet[3], mapped_time,
                    format_values(accel), format_values(gyro),
                    format_values(icm_accel), format_values(icm_gyro),
                    age_ms, saturation,
                    packet[12], packet[13], spi_errors, sync_errors, m4_ms,
                ),
                flush=True,
            )
            frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        mpu.close()
        icm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
