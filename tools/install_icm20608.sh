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

scp $SSH_OPTIONS "$PROJECT_ROOT/tools/icm20608-load.sh" \
    "$PROJECT_ROOT/tools/icm20608-load.service" "$HOST:/tmp/"

ssh $SSH_OPTIONS "$HOST" '
set -eu
mkdir -p /usr/local/sbin
install -m 0755 /tmp/icm20608-load.sh /usr/local/sbin/icm20608-load
install -m 0644 /tmp/icm20608-load.service /etc/systemd/system/icm20608-load.service
systemctl daemon-reload
systemctl enable icm20608-load.service
systemctl restart icm20608-load.service
printf "enabled="
systemctl is-enabled icm20608-load.service
printf "active="
systemctl is-active icm20608-load.service
ls -l /dev/icm20608
'
