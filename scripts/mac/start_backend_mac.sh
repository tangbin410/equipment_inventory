#!/usr/bin/env bash

# 用途：单独启动盘点 API 后端，读取根目录 .env，默认监听 127.0.0.1:8000。
# 使用：由 start_all_mac.command 和 switch_mode_mac.command 调用，也可在终端独立运行。
# 影响：仅启动后端；不修改配置、不切换模式、不启停隧道，远程页面由此后端按配置提供。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  echo "Python environment is missing. Run ./scripts/mac/setup_mac.sh first."
  exit 1
fi

if [[ ! -f .env ]]; then
  echo ".env is missing. Copy the original Windows .env before starting."
  exit 1
fi

HOST="${INVENTORY_HOST:-127.0.0.1}"
PORT="${INVENTORY_PORT:-8000}"

exec .venv/bin/python -m uvicorn backend.main:app \
  --env-file .env \
  --host "$HOST" \
  --port "$PORT" \
  --reload
