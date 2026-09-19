#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOST=${1:-mp157}
INTERFACE=${2:-en7}
CONTROL_PATH=${3:-none}
SSH_OPTIONS="-o BindInterface=$INTERFACE -o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2"
if [ "$CONTROL_PATH" != none ]; then
    SSH_OPTIONS="$SSH_OPTIONS -o ControlPath=$CONTROL_PATH"
fi
FIRMWARE="$PROJECT_ROOT/m4/build-arm/m4_rpmsg.elf"

test -f "$FIRMWARE"
scp $SSH_OPTIONS "$FIRMWARE" \
    "$PROJECT_ROOT/tools/m4-rpmsg-autostart.sh" \
    "$PROJECT_ROOT/tools/m4-rpmsg.service" "$HOST:/tmp/"

ssh $SSH_OPTIONS "$HOST" '
set -eu
mkdir -p /usr/local/sbin
install -m 0644 /tmp/m4_rpmsg.elf /lib/firmware/m4_rpmsg.elf
install -m 0755 /tmp/m4-rpmsg-autostart.sh /usr/local/sbin/m4-rpmsg-autostart
install -m 0644 /tmp/m4-rpmsg.service /etc/systemd/system/m4-rpmsg.service
systemctl daemon-reload
systemctl enable m4-rpmsg.service
if [ "$(cat /sys/class/remoteproc/remoteproc0/state)" != running ] ||
   [ "$(cat /sys/class/remoteproc/remoteproc0/firmware)" != m4_rpmsg.elf ]; then
    systemctl restart m4-rpmsg.service
fi
systemctl is-enabled m4-rpmsg.service
systemctl is-active m4-rpmsg.service
cat /sys/class/remoteproc/remoteproc0/state
cat /sys/class/remoteproc/remoteproc0/firmware
'
