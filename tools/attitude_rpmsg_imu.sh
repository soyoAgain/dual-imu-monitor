#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REMOTE_SCRIPT=/tmp/attitude_rpmsg_imu.py

stop_remote() {
  trap - INT TERM HUP
  ssh -o ConnectTimeout=3 mp157 \
    "pkill -TERM -f '^python3 $REMOTE_SCRIPT( |$)' || true" \
    >/dev/null 2>&1 || true
  printf '\n已停止远程姿态解算。\n'
  exit 130
}

trap stop_remote INT TERM HUP
scp "$PROJECT_ROOT/tools/attitude_rpmsg_imu.py" "mp157:$REMOTE_SCRIPT"
ssh -tt mp157 exec python3 "$REMOTE_SCRIPT" "$@"
