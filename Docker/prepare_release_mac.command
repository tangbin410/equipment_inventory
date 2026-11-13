#!/usr/bin/env bash
# 用途：在本机编译自研Python及网页，只生成Docker发布包；不启动/停止现有服务。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${INVENTORY_PACK_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "需要 Python 3.12。请设置 INVENTORY_PACK_PYTHON 指向该解释器。"
  exit 1
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/prepare_release.py" "$@"
