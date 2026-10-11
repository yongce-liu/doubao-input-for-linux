#!/bin/sh
VENV="$HOME/.local/share/doubao-input/.venv"
if [ ! -x "$VENV/bin/python" ]; then
    echo '请先按安装说明使用 uv 初始化 ~/.local/share/doubao-input/.venv' >&2
    exit 1
fi
exec "$VENV/bin/python" -m doubao_input "$@"
