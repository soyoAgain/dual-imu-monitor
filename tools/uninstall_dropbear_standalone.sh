#!/bin/sh
set -eu

HOST=${1:-mp157}
INTERFACE=${2:-en7}
SSH_OPTIONS="-o BindInterface=$INTERFACE -o ConnectTimeout=5"

ssh $SSH_OPTIONS "$HOST" '
set -eu
systemctl disable dropbear.service
systemctl stop dropbear.service
rm -f /etc/systemd/system/dropbear.service
systemctl daemon-reload
systemctl enable dropbear.socket
systemctl start dropbear.socket
'
