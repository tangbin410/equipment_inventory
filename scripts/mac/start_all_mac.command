#!/usr/bin/env bash

# 用途：Mac 日常一键启动入口，按需启动后端、本机后台和本地 AI Agent，并打开后台。
# 调用：缺环境时运行 setup_mac.sh，再调用 start_backend_mac.sh、start_admin_mac.sh、start_agent_mac.sh。
# 影响：复用已运行服务；退出时只停止本脚本新启动的服务，不切换模式、不启停公网隧道。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  echo "启动失败：项目根目录缺少 .env 文件。"
  echo "请先将原 Windows 系统的 .env 复制到此目录。"
  read -r -p "按回车键退出..."
  exit 1
fi

if [[ ! -x .venv/bin/python || ! -d admin_web/node_modules ]]; then
  echo "首次运行，正在初始化开发环境..."
  "$SCRIPT_DIR/setup_mac.sh"
fi

PIDS=()
LOG_DIR="${TMPDIR:-/tmp}/equipment_inventory_logs"
mkdir -p "$LOG_DIR"

cleanup() {
  if [[ ${#PIDS[@]} -eq 0 ]]; then
    return
  fi
  echo
  echo "正在停止全部服务..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start_service() {
  local name="$1"
  local script="$2"
  local log_file="$LOG_DIR/$3"
  local health_url="$4"

  if curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 "$health_url" >/dev/null 2>&1; then
    echo "${name}已在运行，继续使用现有服务。"
    return
  fi

  echo "正在启动${name}..."
  "$SCRIPT_DIR/$script" >"$log_file" 2>&1 &
  PIDS+=("$!")
  echo "  日志：$log_file"
}

start_service "后端服务" "start_backend_mac.sh" "backend.log" "http://127.0.0.1:8000/health"
start_service "管理端" "start_admin_mac.sh" "admin.log" "http://127.0.0.1:5173/"

if [[ -f local_agent/app.py ]]; then
  start_service "本地 AI Agent" "start_agent_mac.sh" "agent.log" "http://127.0.0.1:9000/health"
fi

if [[ ${#PIDS[@]} -eq 0 ]]; then
  echo "全部服务已在运行，无需重复启动。"
  open http://127.0.0.1:5173
  exit 0
fi

echo
echo "服务已启动："
echo "  管理端：http://127.0.0.1:5173"
echo "  后端：http://127.0.0.1:8000/health"
if [[ -f local_agent/app.py ]]; then
  echo "  AI Agent：http://127.0.0.1:9000/health"
fi
echo "按 Control-C 可一次停止全部服务。"

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:5173 >/dev/null 2>&1; then
    open http://127.0.0.1:5173
    break
  fi
  sleep 1
done

while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "有服务意外退出，请检查日志目录：$LOG_DIR"
      exit 1
    fi
  done
  sleep 2
done
