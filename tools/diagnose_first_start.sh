#!/bin/sh
# Run on the board after a fresh boot. Capture evidence even if SSH disappears.
set -eu
log=/tmp/m4-first-start.log
exec > "$log" 2>&1
r=/sys/class/remoteproc/remoteproc0
snapshot() {
  echo "=== $1 ==="
  /usr/bin/uptime
  /sbin/ip -br addr
  /sbin/ip -s link show eth0
  cat "$r/state"
  python3 - <<'PY'
import mmap
import struct
with open('/dev/mem', 'rb', buffering=0) as f:
    with mmap.mmap(f.fileno(), 4096, flags=mmap.MAP_SHARED,
                   prot=mmap.PROT_READ, offset=0x50003000) as m:
        for name, offset in [('MODER', 0), ('OTYPER', 4), ('OSPEEDR', 8),
                             ('PUPDR', 12), ('IDR', 16), ('ODR', 20),
                             ('AFRL', 32), ('AFRH', 36)]:
            print('GPIOB %s=0x%08x' % (name, struct.unpack_from('<I', m, offset)[0]))
PY
}
test "$(cat "$r/state")" = offline
test -r /lib/firmware/m4_mpu6050_no_tx.elf
started=0
finish() {
  result=$?
  trap - EXIT
  if [ "$started" = 1 ] && [ "$(cat "$r/state")" = running ]; then
    echo stop > "$r/state" || true
  fi
  echo "DIAG_EXIT=$result"
  cat "$log" > /dev/ttySTM0 || true
  exit "$result"
}
trap finish EXIT
snapshot before_unbind
d=/sys/bus/platform/drivers/stm32-usart
test ! -e "$d/40011000.serial" || echo 40011000.serial > "$d/unbind"
snapshot before_start
echo m4_mpu6050_no_tx.elf > "$r/firmware"
started=1
echo start > "$r/state"
sleep 3
snapshot after_start
echo stop > "$r/state"
snapshot after_stop
/bin/dmesg | tail -n 30
# The EXIT handler copies evidence to the console even if a snapshot fails.
