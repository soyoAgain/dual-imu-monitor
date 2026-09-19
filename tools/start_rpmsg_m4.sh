#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SSH_OPTIONS="-o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2"

cmake -S "$PROJECT_ROOT/m4" -B "$PROJECT_ROOT/m4/build-arm" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi-toolchain.cmake
cmake --build "$PROJECT_ROOT/m4/build-arm" --target m4_rpmsg.elf

scp $SSH_OPTIONS "$PROJECT_ROOT/m4/build-arm/m4_rpmsg.elf" \
  mp157:/lib/firmware/m4_rpmsg.elf
scp $SSH_OPTIONS "$PROJECT_ROOT/tools/read_rpmsg_imu.py" \
  mp157:/tmp/read_rpmsg_imu.py

ssh $SSH_OPTIONS mp157 '
set -eu
r=/sys/class/remoteproc/remoteproc0
driver=/sys/bus/platform/drivers/stm32-usart
device=40011000.serial
[ "$(cat "$r/state")" = running ] && echo stop > "$r/state" || true
[ -e "$driver/$device" ] && echo "$device" > "$driver/unbind" || true
echo m4_rpmsg.elf > "$r/firmware"
echo start > "$r/state"
i=0
while [ ! -e /dev/ttyRPMSG0 ] && [ "$i" -lt 10 ]; do
  sleep 1
  i=$((i + 1))
done
test -e /dev/ttyRPMSG0
cat "$r/state"
python3 /tmp/read_rpmsg_imu.py --count 250 --timeout 12
'
