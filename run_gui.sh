#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# 可用 PYTHON_BIN 指定解释器；默认使用本机 miniforge 的 pytorch_env。
PYTHON_BIN=${PYTHON_BIN:-"$HOME/miniforge3/envs/pytorch_env/bin/python"}

if [ ! -x "$PYTHON_BIN" ]; then
    echo "错误：找不到 Python：$PYTHON_BIN" >&2
    echo "请先安装 PyQt5/NumPy/Matplotlib，或用 PYTHON_BIN 指定解释器。" >&2
    exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/gui/app.py" "$@"
