#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOST=${1:-mp157}
INTERFACE=${2:-en7}
SSH_OPTIONS="-o BindInterface=$INTERFACE -o ConnectTimeout=5"

scp $SSH_OPTIONS "$PROJECT_ROOT/tools/dropbear-standalone.service" \
    "$HOST:/tmp/dropbear.service"

ssh $SSH_OPTIONS "$HOST" '
set -eu
install -m 0644 /tmp/dropbear.service /etc/systemd/system/dropbear.service
systemctl daemon-reload
systemctl disable dropbear.socket
systemctl enable dropbear.service
systemctl stop dropbear.socket
systemctl start dropbear.service
'
