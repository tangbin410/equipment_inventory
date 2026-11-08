#!/usr/bin/env bash

# 用途：构建异地后台正式页面到 admin_web/remote_dist，并检查配置与本机后端。
# 使用：更新后台页面后双击；--check-only 仅自检，不构建。属于维护工具，平时无需运行。
# 影响：成功构建后替换正式页面；不修改 .env、不切换模式、不启停后端或隧道。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || exit 1
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
REMOTE_DIST="$PROJECT_DIR/admin_web/remote_dist"
STAGE_DIR=""
BACKUP_DIR=""

finish() {
  local status=$?
  if [[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]]; then rm -rf "$STAGE_DIR"; fi
  if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" && ! -e "$BACKUP_DIR/site" ]]; then
    rmdir "$BACKUP_DIR" 2>/dev/null || true
  fi
  if [[ -t 0 ]]; then
    echo
    read -r -p "按回车键关闭窗口……" _reply || true
  fi
  return "$status"
}
trap finish EXIT

CHECK_ONLY=0
case "${1:-}" in
  "") ;;
  --check-only) CHECK_ONLY=1 ;;
  *) echo "用法：双击此文件准备后台，或使用 --check-only 仅检查。"; exit 2 ;;
esac

echo "资产盘点 · 异地后台准备与自检"
echo "本工具不会修改配置、切换小程序模式，也不会重启后端或公网连接。"
echo

if [[ -L "$REMOTE_DIST" || ( -e "$REMOTE_DIST" && ! -d "$REMOTE_DIST" ) ]]; then
  echo "remote_dist 不是普通目录，请先人工确认；本工具不会替换符号链接或其他文件。"
  exit 1
fi

if [[ $CHECK_ONLY -eq 0 ]]; then
  echo "[1/3] 构建异地后台页面……"
  if ! command -v npm >/dev/null 2>&1; then
    echo "未找到 npm，请先安装 Node.js，并运行 scripts/mac/setup_mac.sh。"
    exit 1
  fi
  STAGE_DIR="$(mktemp -d "$PROJECT_DIR/admin_web/.remote-admin-build.XXXXXX")" || exit 1
  if ! VITE_API_BASE=/api npm --prefix admin_web run build -- --outDir "$STAGE_DIR"; then
    echo "页面构建未完成，请查看上方错误；原有页面、服务和配置未改动。"
    exit 1
  fi
  if [[ ! -f "$STAGE_DIR/index.html" ]]; then
    echo "构建目录缺少 index.html，保留原有页面，请检查构建配置。"
    exit 1
  fi
  if [[ -d "$REMOTE_DIST" ]]; then
    BACKUP_DIR="$(mktemp -d "$PROJECT_DIR/admin_web/.remote-admin-previous.XXXXXX")" || exit 1
    if ! mv "$REMOTE_DIST" "$BACKUP_DIR/site"; then
      echo "无法暂存原有页面，已停止更新，请检查目录权限。"
      exit 1
    fi
  fi
  if ! mv "$STAGE_DIR" "$REMOTE_DIST"; then
    echo "新页面替换失败，正在恢复原有页面……"
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR/site" ]]; then
      if mv "$BACKUP_DIR/site" "$REMOTE_DIST"; then
        echo "原有页面已恢复。"
      else
        echo "自动恢复未成功，请保留并人工恢复此备份：$BACKUP_DIR/site"
      fi
    fi
    exit 1
  fi
  STAGE_DIR=""
  if [[ -n "$BACKUP_DIR" ]]; then rm -rf "$BACKUP_DIR"; BACKUP_DIR=""; fi
else
  echo "[1/3] 仅检查已有页面，不重新构建。"
fi

READY=1
if [[ -f admin_web/remote_dist/index.html ]]; then
  echo "页面文件已准备：admin_web/remote_dist"
else
  echo "尚未生成异地后台页面，请不带 --check-only 重新运行本工具。"
  READY=0
fi

echo
echo "[2/3] 检查异地后台配置……"
if [[ ! -x .venv/bin/python ]]; then
  echo "缺少项目 Python 环境，请先运行 scripts/mac/setup_mac.sh。"
  READY=0
else
  if ! .venv/bin/python - <<'PY'
import os
import sys
from pathlib import Path

try:
    from dotenv import dotenv_values
    from backend.admin_access import validate_remote_admin_config
except ImportError:
    print("配置自检依赖不完整，请先运行 scripts/mac/setup_mac.sh 安装依赖。")
    sys.exit(2)

env_file = Path(".env")
if env_file.exists():
    for name, value in dotenv_values(env_file).items():
        if value is not None:
            os.environ.setdefault(name, value)
else:
    print("未找到 .env 文件，本次仅检查当前进程环境配置。")

issues = []
if os.getenv("INVENTORY_REMOTE_ADMIN_ENABLED", "0").strip() != "1":
    issues.append("INVENTORY_REMOTE_ADMIN_ENABLED：异地后台尚未启用，应设置为 1。")
for name in ("INVENTORY_ADMIN_HOST", "CLOUDFLARE_ACCESS_TEAM_DOMAIN", "CLOUDFLARE_ACCESS_AUD", "INVENTORY_ADMIN_ALLOWED_EMAILS"):
    if not os.getenv(name, "").strip():
        issues.append(f"{name}：尚未配置。")
if not issues:
    try:
        validate_remote_admin_config()
    except RuntimeError as exc:
        issues.append(str(exc))
if issues:
    for issue in issues:
        print("- " + issue)
    print("配置尚未就绪；已生成的页面文件可以保留，补齐后再次自检即可。")
    sys.exit(2)
print("异地后台配置校验通过（配置值已隐藏）。")
PY
  then
    READY=0
  fi
fi

echo
echo "[3/3] 检查本机后端 HTTP 响应……"
if [[ -x .venv/bin/python ]] && command -v curl >/dev/null 2>&1 \
  && HEALTH_RESPONSE="$(curl --noproxy '*' -fsS --connect-timeout 1 --max-time 3 http://127.0.0.1:8000/health 2>/dev/null)" \
  && printf '%s' "$HEALTH_RESPONSE" | .venv/bin/python -c 'import json,sys; value=json.load(sys.stdin); sys.exit(0 if isinstance(value,dict) and value.get("status")=="ok" else 1)' 2>/dev/null; then
  echo "本机 8000 端口返回预期健康格式；本工具不据此识别或管理占用端口的进程。"
else
  echo "本机后端未返回预期健康格式，请确认服务后手动运行 scripts/mac/start_backend_mac.sh。"
  echo "若端口被其他程序占用，请先人工确认；本工具不会终止任何进程。"
  READY=0
fi

echo
echo "本机检查不代表公网已可访问；Cloudflare 域名、Access 规则和隧道仍需单独配置验证。"
if [[ $READY -eq 1 ]]; then
  echo "本机准备与自检通过。"
  exit 0
fi
echo "页面准备或自检尚有未完成项，请按上方提示处理后再次运行。"
exit 2
