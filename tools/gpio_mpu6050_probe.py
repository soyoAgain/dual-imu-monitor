#!/usr/bin/env python3
"""Probe an MPU6050 on the ATK JP12 connector using GPIO bit-banged I2C.

The ATK connector routes the module communication pins to UART5 on PB12/PB13.
This script temporarily unbinds UART5, probes both possible SDA/SCL orientations,
then restores UART5 before exiting.
"""

import os
import time


GPIO_ROOT = "/sys/class/gpio"
UART_DRIVER = "/sys/bus/platform/drivers/stm32-usart"
UART_DEVICE = "40011000.serial"
PB12 = 28
PB13 = 29
WHO_AM_I = 0x75
ADDRESSES = (0x68, 0x69)
DELAY = 0.00002


def write_text(path: str, value: str) -> None:
    with open(path, "w", encoding="ascii") as stream:
        stream.write(value)


class GPIO:
    def __init__(self, number: int):
        self.number = number
        self.path = f"{GPIO_ROOT}/gpio{number}"
        self.exported_here = False

    def export(self) -> None:
        if not os.path.isdir(self.path):
            write_text(f"{GPIO_ROOT}/export", str(self.number))
            self.exported_here = True
            for _ in range(100):
                if os.path.isdir(self.path):
                    break
                time.sleep(0.01)
            else:
                raise RuntimeError(f"GPIO {self.number} did not appear")
        self.release()

    def release(self) -> None:
        write_text(f"{self.path}/direction", "in")

    def low(self) -> None:
        write_text(f"{self.path}/direction", "low")

    def read(self) -> int:
        with open(f"{self.path}/value", "r", encoding="ascii") as stream:
            return 1 if stream.read(1) == "1" else 0

    def cleanup(self) -> None:
        if os.path.isdir(self.path):
            self.release()
        if self.exported_here:
            write_text(f"{GPIO_ROOT}/unexport", str(self.number))


class BitBangI2C:
    def __init__(self, sda: GPIO, scl: GPIO):
        self.sda = sda
        self.scl = scl

    @staticmethod
    def delay() -> None:
        time.sleep(DELAY)

    def line(self, gpio: GPIO, high: bool) -> None:
        gpio.release() if high else gpio.low()
        self.delay()

    def start(self) -> None:
        self.line(self.sda, True)
        self.line(self.scl, True)
        self.line(self.sda, False)
        self.line(self.scl, False)

    def stop(self) -> None:
        self.line(self.sda, False)
        self.line(self.scl, True)
        self.line(self.sda, True)

    def write_byte(self, value: int) -> bool:
        for bit in range(7, -1, -1):
            self.line(self.sda, bool(value & (1 << bit)))
            self.line(self.scl, True)
            self.line(self.scl, False)
        self.line(self.sda, True)
        self.line(self.scl, True)
        acknowledged = self.sda.read() == 0
        self.line(self.scl, False)
        return acknowledged

    def read_byte(self, acknowledge: bool) -> int:
        value = 0
        self.line(self.sda, True)
        for _ in range(8):
            self.line(self.scl, True)
            value = (value << 1) | self.sda.read()
            self.line(self.scl, False)
        self.line(self.sda, not acknowledge)
        self.line(self.scl, True)
        self.line(self.scl, False)
        self.line(self.sda, True)
        return value

    def read_register(self, address: int, register: int) -> int:
        try:
            self.start()
            if not self.write_byte(address << 1):
                raise OSError("no ACK after write address")
            if not self.write_byte(register):
                raise OSError("no ACK after register address")
            self.start()
            if not self.write_byte((address << 1) | 1):
                raise OSError("no ACK after read address")
            return self.read_byte(acknowledge=False)
        finally:
            self.stop()


def unbind_uart5() -> bool:
    path = f"{UART_DRIVER}/{UART_DEVICE}"
    if not os.path.exists(path):
        return False
    write_text(f"{UART_DRIVER}/unbind", UART_DEVICE)
    return True


def bind_uart5() -> None:
    write_text(f"{UART_DRIVER}/bind", UART_DEVICE)


def main() -> int:
    gpios = {number: GPIO(number) for number in (PB12, PB13)}
    uart_was_bound = False
    found = []
    try:
        uart_was_bound = unbind_uart5()
        for gpio in gpios.values():
            gpio.export()

        orientations = (
            ("SDA=PB12,SCL=PB13", PB12, PB13),
            ("SDA=PB13,SCL=PB12", PB13, PB12),
        )
        for label, sda_number, scl_number in orientations:
            bus = BitBangI2C(gpios[sda_number], gpios[scl_number])
            gpios[sda_number].release()
            gpios[scl_number].release()
            time.sleep(0.001)
            idle_sda = gpios[sda_number].read()
            idle_scl = gpios[scl_number].read()
            print(
                f"Probing {label}; idle SDA={idle_sda}, SCL={idle_scl}"
            )
            if not idle_sda or not idle_scl:
                print("SKIP: bus is stuck low or has no effective pull-up")
                continue
            for address in ADDRESSES:
                try:
                    value = bus.read_register(address, WHO_AM_I)
                except OSError as exc:
                    print(f"MISS address=0x{address:02x}: {exc}")
                    continue
                print(
                    f"REPLY address=0x{address:02x} "
                    f"WHO_AM_I=0x{value:02x}"
                )
                if value in ADDRESSES:
                    found.append((label, address, value))
    finally:
        for gpio in gpios.values():
            try:
                gpio.cleanup()
            except OSError as exc:
                print(f"WARN: GPIO {gpio.number} cleanup failed: {exc}")
        if uart_was_bound:
            try:
                bind_uart5()
            except OSError as exc:
                print(f"WARN: UART5 restore failed: {exc}")

    if not found:
        print("RESULT: MPU6050 not detected on JP12")
        return 1

    for label, address, value in found:
        print(
            f"RESULT: MPU6050 detected ({label}), "
            f"address=0x{address:02x}, WHO_AM_I=0x{value:02x}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
