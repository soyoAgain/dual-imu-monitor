#!/usr/bin/env python3
"""Sample STM32MP157 GPIOF ODR to verify the M4 user LED firmware."""

import mmap
import struct
import time


GPIOF_BASE = 0x50007000
GPIO_ODR_OFFSET = 0x14
USER_LED_MASK = 1 << 3


def main() -> None:
    with open("/dev/mem", "rb", buffering=0) as memory:
        registers = mmap.mmap(
            memory.fileno(),
            mmap.PAGESIZE,
            flags=mmap.MAP_SHARED,
            prot=mmap.PROT_READ,
            offset=GPIOF_BASE,
        )
        try:
            for sample in range(30):
                value = struct.unpack_from("<I", registers, GPIO_ODR_OFFSET)[0]
                pin = 1 if value & USER_LED_MASK else 0
                led = "OFF" if pin else "ON"
                print(f"{sample:02d}: ODR=0x{value:08x} PF3={pin} LED={led}")
                time.sleep(0.1)
        finally:
            registers.close()


if __name__ == "__main__":
    main()
