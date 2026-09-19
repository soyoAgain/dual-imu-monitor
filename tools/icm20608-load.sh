#!/bin/sh
set -eu

case "${1:-}" in
  start)
    if [ ! -e /dev/icm20608 ]; then
      /bin/busybox modprobe icm20608
    fi
    ;;
  stop)
    if [ -e /dev/icm20608 ]; then
      /bin/busybox rmmod icm20608
    fi
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 1
    ;;
esac
