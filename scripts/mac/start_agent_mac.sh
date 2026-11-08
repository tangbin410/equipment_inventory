#!/usr/bin/env bash

# 用途：单独启动本地 AI Agent，监听 127.0.0.1:9000，并读取项目根目录 .env。
# 使用：由 start_all_mac.command 调用，也可在需要 AI 辅助分析时独立运行。
# 影响：只启动 AI 服务；不启动主后端、不切换模式、不启停公网隧道。
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

exec .venv/bin/python -m uvicorn local_agent.app:app \
  --env-file .env \
  --host 127.0.0.1 \
  --port 9000 \
  --reload
