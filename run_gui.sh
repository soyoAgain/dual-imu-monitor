#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=/Users/xiechushu/miniforge3/envs/pytorch_env/bin/python

if [ ! -x "$PYTHON_BIN" ]; then
    echo "错误：找不到 pytorch_env Python：$PYTHON_BIN" >&2
    exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/gui/app.py" "$@"
