#!/usr/bin/env python3
"""Continuously log the MP157 USB-TTL console to a file.

Usage: python3 tools/serial_logger.py <logfile> [port]
Keeps running until killed; reopens the port if it disappears.
"""
import os
import sys
import time
import termios
import select

PORT = '/dev/cu.usbserial-1140'
BAUD = termios.B115200


def open_port(port):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = termios.IGNBRK
    a[1] = 0
    a[2] = (a[2] & ~termios.CSIZE) | termios.CS8 | termios.CREAD | termios.CLOCAL
    a[3] = 0
    a[4] = a[5] = BAUD
    cc = list(a[6])
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    a[6] = cc
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def main():
    logpath = sys.argv[1]
    port = sys.argv[2] if len(sys.argv) > 2 else PORT
    log = open(logpath, 'ab', buffering=0)
    log.write(('### logger started %s ###\n' % time.strftime('%Y-%m-%d %H:%M:%S')).encode())
    fd = None
    while True:
        if fd is None:
            try:
                fd = open_port(port)
                log.write(b'### port opened ###\n')
            except OSError:
                time.sleep(1)
                continue
        try:
            r, _, _ = select.select([fd], [], [], 0.5)
            if r:
                chunk = os.read(fd, 4096)
                if chunk:
                    log.write(chunk)
        except OSError:
            log.write(b'### port lost, reopening ###\n')
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
            time.sleep(1)


if __name__ == '__main__':
    main()
