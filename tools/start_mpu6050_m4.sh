#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

cmake -S "$PROJECT_ROOT/m4" -B "$PROJECT_ROOT/m4/build-arm" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi-toolchain.cmake
cmake --build "$PROJECT_ROOT/m4/build-arm" --target m4_mpu6050.elf

scp "$PROJECT_ROOT/m4/build-arm/m4_mpu6050.elf" \
  mp157:/lib/firmware/m4_mpu6050.elf
scp "$PROJECT_ROOT/tools/read_m4_mpu6050_status.py" \
  mp157:/tmp/read_m4_mpu6050_status.py

ssh mp157 '
set -eu
r=/sys/class/remoteproc/remoteproc0
driver=/sys/bus/platform/drivers/stm32-usart
device=40011000.serial
[ "$(cat "$r/state")" = running ] && echo stop > "$r/state" || true
[ -e "$driver/$device" ] && echo "$device" > "$driver/unbind" || true
echo m4_mpu6050.elf > "$r/firmware"
echo start > "$r/state"
cat "$r/state"
python3 /tmp/read_m4_mpu6050_status.py
'
