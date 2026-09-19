#!/bin/sh
set -eu

RPROC=/sys/class/remoteproc/remoteproc0
UART_DRIVER=/sys/bus/platform/drivers/stm32-usart
UART_DEVICE=40011000.serial
FIRMWARE=m4_rpmsg.elf

wait_for_remoteproc() {
    count=0
    while [ ! -e "$RPROC/state" ] && [ "$count" -lt 20 ]; do
        sleep 1
        count=$((count + 1))
    done
    test -e "$RPROC/state"
}

start_m4() {
    wait_for_remoteproc
    state=$(cat "$RPROC/state")
    selected=$(cat "$RPROC/firmware")
    if [ "$state" = running ] && [ "$selected" = "$FIRMWARE" ]; then
        exit 0
    fi
    if [ "$state" = running ]; then
        echo stop > "$RPROC/state"
    fi
    if [ -e "$UART_DRIVER/$UART_DEVICE" ]; then
        echo "$UART_DEVICE" > "$UART_DRIVER/unbind"
    fi
    echo "$FIRMWARE" > "$RPROC/firmware"
    echo start > "$RPROC/state"
    count=0
    while [ ! -e /dev/ttyRPMSG0 ] && [ "$count" -lt 10 ]; do
        sleep 1
        count=$((count + 1))
    done
    test "$(cat "$RPROC/state")" = running
    test -e /dev/ttyRPMSG0
}

stop_m4() {
    wait_for_remoteproc || exit 0
    if [ "$(cat "$RPROC/state")" = running ]; then
        echo stop > "$RPROC/state"
    fi
}

case "${1:-start}" in
    start) start_m4 ;;
    stop) stop_m4 ;;
    *) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac
