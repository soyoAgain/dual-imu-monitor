#!/usr/bin/env python3
"""Read the MPU6050 probe result published by the Cortex-M4 in MCU SRAM."""

import mmap
import struct
import time


MCU_SRAM_BASE = 0x10020000
STATUS_OFFSET = 0x100
STATUS_FORMAT = "<11I7iI"


def main() -> None:
    with open("/dev/mem", "rb", buffering=0) as memory:
        region = mmap.mmap(
            memory.fileno(),
            mmap.PAGESIZE,
            flags=mmap.MAP_SHARED,
            prot=mmap.PROT_READ,
            offset=MCU_SRAM_BASE,
        )
        try:
            for _ in range(20):
                fields = struct.unpack_from(STATUS_FORMAT, region, STATUS_OFFSET)
                (
                    magic, version, sequence, address, who_am_i, idle_lines,
                    scheduled_ms, timestamp_ms, period_ms, overruns,
                    i2c_errors, accel_x, accel_y, accel_z, temperature,
                    gyro_x, gyro_y, gyro_z, last_error,
                ) = fields
                print(
                    f"magic=0x{magic:08x} version={version} sequence={sequence} "
                    f"address=0x{address:02x} WHO_AM_I=0x{who_am_i:02x} "
                    f"scheduled_ms={scheduled_ms} timestamp_ms={timestamp_ms} "
                    f"period_ms={period_ms} overruns={overruns} "
                    f"i2c_errors={i2c_errors} last_error={last_error} "
                    f"accel=({accel_x},{accel_y},{accel_z}) "
                    f"gyro=({gyro_x},{gyro_y},{gyro_z}) "
                    f"temperature_raw={temperature} "
                    f"idle_SDA={idle_lines & 1} idle_SCL={(idle_lines >> 1) & 1}"
                )
                if magic == 0x4D505536 and version == 2 and sequence > 0:
                    break
                time.sleep(0.1)
        finally:
            region.close()


if __name__ == "__main__":
    main()
