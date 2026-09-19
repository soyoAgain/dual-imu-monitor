#!/usr/bin/env python3
"""Read STM32MP1 GPIO bank registers via /dev/mem.

The GPIO banks are clock-gated by Linux at runtime, so a plain read returns
zeros.  This tool temporarily enables the requested bank clocks (RCC
MP_AHB4ENSETR, the Cortex-A7 gate) and restores the previous clock state
afterwards.  The Cortex-M4 gate (MC_AHB4ENSETR) is not touched.

Usage (on the board, as root):
    python3 gpio_bank_dump.py            # default: A,C,E,G
    python3 gpio_bank_dump.py A B C E G  # explicit banks
"""
import mmap
import struct
import sys

RCC_BASE = 0x50000000
# Linux (Cortex-A7) 管理 MP_AHB4ENSETR；MC_AHB4ENSETR 由 M4 固件使用。
MP_AHB4ENSETR = 0x0A28
MP_AHB4ENCLRR = 0x0A2C
BANK_BASE = {
    'A': 0x50002000, 'B': 0x50003000, 'C': 0x50004000, 'D': 0x50005000,
    'E': 0x50006000, 'F': 0x50007000, 'G': 0x50008000, 'H': 0x50009000,
    'I': 0x5000A000, 'J': 0x5000B000, 'K': 0x5000C000,
}
REGS = [('MODER', 0), ('OTYPER', 4), ('OSPEEDR', 8), ('PUPDR', 12),
        ('IDR', 16), ('ODR', 20), ('AFRL', 32), ('AFRH', 36)]


def f2(value, pin):
    return (value >> (2 * pin)) & 3


def f4(afrl, afrh, pin):
    return ((afrl >> (4 * pin)) & 0xF) if pin < 8 else ((afrh >> (4 * (pin - 8))) & 0xF)


def main():
    banks = sys.argv[1:] or ['A', 'C', 'E', 'G']
    banks = [b.upper() for b in banks]
    with open('/dev/mem', 'r+b', buffering=0) as mem:
        rcc = mmap.mmap(mem.fileno(), 4096, flags=mmap.MAP_SHARED,
                        prot=mmap.PROT_READ | mmap.PROT_WRITE, offset=RCC_BASE)
        before = struct.unpack_from('<I', rcc, MP_AHB4ENSETR)[0]
        want = 0
        for b in banks:
            want |= 1 << (ord(b) - ord('A'))
        rcc[MP_AHB4ENSETR:MP_AHB4ENSETR + 4] = struct.pack('<I', want)
        for b in banks:
            base = BANK_BASE[b]
            bank = mmap.mmap(mem.fileno(), 4096, flags=mmap.MAP_SHARED,
                             prot=mmap.PROT_READ, offset=base)
            vals = {name: struct.unpack_from('<I', bank, off)[0] for name, off in REGS}
            print('=== GPIO%s base=0x%08x ===' % (b, base))
            for name, _ in REGS:
                print('GPIO%s %s=0x%08x' % (b, name, vals[name]))
            print('pin  MODER OTYPER OSPEEDR PUPDR AF')
            for p in range(16):
                af = f4(vals['AFRL'], vals['AFRH'], p)
                print('P%s%-2d  %2d    %2d      %2d     %2d   %2d (0x%x)' % (
                    b, p, f2(vals['MODER'], p), f2(vals['OTYPER'], p),
                    f2(vals['OSPEEDR'], p), f2(vals['PUPDR'], p), af, af))
            bank.close()
        restore = before & want
        if restore != want:
            rcc[MP_AHB4ENCLRR:MP_AHB4ENCLRR + 4] = struct.pack('<I', want & ~restore)
        rcc.close()


if __name__ == '__main__':
    main()
