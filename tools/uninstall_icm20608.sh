#!/bin/sh
set -eu

HOST=${1:-mp157}
INTERFACE=${2:-en7}
CONTROL_PATH=${3:-none}
SSH_OPTIONS="-o BindInterface=$INTERFACE -o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2"
if [ "$CONTROL_PATH" != none ]; then
    SSH_OPTIONS="$SSH_OPTIONS -o ControlPath=$CONTROL_PATH"
fi

ssh $SSH_OPTIONS "$HOST" '
set -eu
systemctl disable --now icm20608-load.service 2>/dev/null || true
rm -f /etc/systemd/system/icm20608-load.service /usr/local/sbin/icm20608-load
systemctl daemon-reload
if [ -e /dev/icm20608 ]; then
    /bin/busybox rmmod icm20608 || true
fi
if [ -e /dev/icm20608 ]; then echo "device=still-present"; else echo "device=removed"; fi
printf "enabled="
systemctl is-enabled icm20608-load.service 2>&1 || true
'
