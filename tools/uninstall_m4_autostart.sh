#!/bin/sh
set -eu

HOST=${1:-mp157}
INTERFACE=${2:-en7}
ssh -o "BindInterface=$INTERFACE" -o ConnectTimeout=5 "$HOST" '
set -eu
systemctl disable --now m4-rpmsg.service 2>/dev/null || true
rm -f /etc/systemd/system/m4-rpmsg.service
rm -f /usr/local/sbin/m4-rpmsg-autostart
systemctl daemon-reload
systemctl reset-failed
'
