#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec uv run --no-sync python -m doubao_input "$@"
