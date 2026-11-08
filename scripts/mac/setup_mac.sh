#!/usr/bin/env bash

# 用途：初始化或更新 Mac 开发依赖，准备 .venv 和 admin_web/node_modules。
# 使用：首次安装、依赖更新或环境缺失时运行；start_all_mac.command 在缺环境时自动调用。
# 影响：安装 Python 依赖并执行 npm ci 重建前端依赖，需要网络；不启动服务、不切换模式。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  echo ".env is missing. Copy the original Windows .env before installing dependencies."
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
elif [[ -x /opt/homebrew/bin/uv ]]; then
  UV_BIN="/opt/homebrew/bin/uv"
else
  echo "uv is not installed. Run: brew install uv"
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  "$UV_BIN" python install 3.12
  "$UV_BIN" venv --python 3.12 .venv
fi

"$UV_BIN" pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -c 'import fastapi, openpyxl, qrcode, requests, uvicorn; print("Mac Python environment is ready.")'

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js is not installed. Run: brew install node"
  exit 1
fi

npm --prefix admin_web ci
echo "Vue admin environment is ready."
