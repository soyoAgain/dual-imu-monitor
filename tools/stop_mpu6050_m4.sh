#!/bin/sh
set -eu

ssh mp157 '
set -eu
r=/sys/class/remoteproc/remoteproc0
driver=/sys/bus/platform/drivers/stm32-usart
device=40011000.serial
[ "$(cat "$r/state")" = running ] && echo stop > "$r/state" || true
[ -e "$driver/$device" ] || echo "$device" > "$driver/bind"
printf "M4="
cat "$r/state"
if [ -e "$driver/$device" ]; then echo "UART5=bound"; else echo "UART5=unbound"; fi
'
