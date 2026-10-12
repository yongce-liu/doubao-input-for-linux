#!/bin/sh
# 发布 Ubuntu 22.04+ 产物时，在 Ubuntu 22.04（同 CPU 架构）构建。
set -eu
cd "$(dirname "$0")/.."
export UV_PROJECT_ENVIRONMENT=.venv-build
uv sync --locked --python "${BUILD_PYTHON:-3.12}"
uv pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" pyinstaller
uv run --no-sync python -m PyInstaller --clean --noconfirm --onefile --name doubao-input \
    --paths src \
    --additional-hooks-dir packaging/hooks \
    --add-data "src/doubao_input/login/resources:doubao_input/login/resources" \
    --exclude-module gi --exclude-module Gtk --exclude-module cairo \
    --exclude-module numpy --hidden-import optparse \
    packaging/binary_entry.py
echo ">>> 完成：dist/doubao-input"
