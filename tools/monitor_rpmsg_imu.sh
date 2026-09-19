#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REMOTE_SCRIPT=/tmp/monitor_rpmsg_imu.py
SSH_OPTIONS="-o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2"

stop_remote_monitor() {
  trap - INT TERM HUP
  ssh $SSH_OPTIONS mp157 \
    "pkill -TERM -f '^python3 $REMOTE_SCRIPT( |$)' || true" \
    >/dev/null 2>&1 || true
  printf '\n已停止远端实时监视。\n'
  exit 130
}

trap stop_remote_monitor INT TERM HUP

if ! ssh $SSH_OPTIONS mp157 true; then
  echo "错误：无法连接 mp157，请检查开发板电源、网线和 IP 地址。" >&2
  exit 1
fi

if ! ssh $SSH_OPTIONS mp157 '
  test "$(cat /sys/class/remoteproc/remoteproc0/state)" = running &&
  test -e /dev/ttyRPMSG0
'; then
  echo "M4/RPMsg 未运行，正在自动启动……"
  sh "$PROJECT_ROOT/tools/start_rpmsg_m4.sh"
fi

scp $SSH_OPTIONS "$PROJECT_ROOT/tools/monitor_rpmsg_imu.py" "mp157:$REMOTE_SCRIPT"
ssh -tt $SSH_OPTIONS mp157 exec python3 "$REMOTE_SCRIPT" "$@"
