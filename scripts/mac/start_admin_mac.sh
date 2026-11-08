#!/usr/bin/env bash

# 用途：单独启动本机 Vue 开发后台（通常为 http://127.0.0.1:5173）。
# 使用：由 start_all_mac.command 调用，也可在终端独立运行；依赖 admin_web/node_modules。
# 影响：仅启动开发页面服务，不构建异地后台、不修改配置、不启停公网隧道。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -d admin_web/node_modules ]]; then
  echo "Vue dependencies are missing. Run ./scripts/mac/setup_mac.sh first."
  exit 1
fi

exec npm --prefix admin_web run dev
