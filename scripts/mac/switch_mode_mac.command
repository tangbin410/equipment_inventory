#!/usr/bin/env bash

# 用途：Mac 双击切换小程序与后端模式；也接受 online（本机测试）或 field（真实微信公网测试）。
# 影响：修改 .env 和小程序配置、重启后端；online 停止隧道，field 启动隧道，失败时尝试恢复。
# 注意：停止隧道也会断开异地后台；切换后需重新编译小程序，手机体验版需重新上传。
# 调用：重启后端使用 start_backend_mac.sh；系统隧道操作可能要求 Mac 管理员授权。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
MINI_ENV_FILE="$PROJECT_DIR/miniprogram/env.js"
LOGIN_FILE="$PROJECT_DIR/miniprogram/pages/login/login.js"
TUNNEL_PLIST="/Library/LaunchDaemons/com.cloudflare.cloudflared.plist"
TUNNEL_SERVICE="system/com.cloudflare.cloudflared"
LOCAL_API="http://localhost:8000/api"
PUBLIC_API="https://api.equip-inventory.xyz/api"
BACKEND_LOG="$PROJECT_DIR/backend_mac.log"
GUI=0
BACKUP_DIR=""
CONFIG_CHANGED=0
BACKEND_WAS_RUNNING=0
TUNNEL_WAS_LOADED=0
LAST_ERROR="切换过程中出现错误。"

fail() {
  LAST_ERROR="$*"
  echo "错误：$*" >&2
  exit 1
}

show_message() {
  /usr/bin/osascript - "$1" <<'APPLESCRIPT'
on run argv
  display dialog (item 1 of argv) with title "资产盘点 · 模式切换" buttons {"好"} default button "好"
end run
APPLESCRIPT
}

run_privileged() {
  /usr/bin/osascript - "$@" <<'APPLESCRIPT'
on run argv
  set commandText to ""
  repeat with argument in argv
    set commandText to commandText & quoted form of (contents of argument) & " "
  end repeat
  do shell script commandText with administrator privileges with prompt "切换资产盘点运行模式：启动或停止小程序公网连接服务。"
end run
APPLESCRIPT
}

tunnel_loaded() {
  /bin/launchctl print "$TUNNEL_SERVICE" >/dev/null 2>&1
}

set_tunnel() {
  local pid deadline
  if [[ "$1" == "start" ]]; then
    if tunnel_loaded; then
      pid="$(/bin/launchctl print "$TUNNEL_SERVICE" | /usr/bin/awk '/^[[:space:]]*pid =/ {print $3}')" || return 1
      if [[ -n "$pid" ]]; then
        echo "公网连接服务已运行，继续使用。"
        return 0
      fi
      run_privileged /bin/launchctl kickstart "$TUNNEL_SERVICE" || return 1
    else
      run_privileged /bin/launchctl bootstrap system "$TUNNEL_PLIST" || return 1
    fi
    echo "公网连接服务已启动。"
  else
    if ! tunnel_loaded; then
      echo "公网连接服务已停止。"
      return 0
    fi
    pid="$(/bin/launchctl print "$TUNNEL_SERVICE" | /usr/bin/awk '/^[[:space:]]*pid =/ {print $3}')" || return 1
    run_privileged /bin/launchctl bootout "$TUNNEL_SERVICE" || return 1
    deadline=$((SECONDS + 35))
    while [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; do
      [[ $SECONDS -lt $deadline ]] || return 1
      sleep 1
    done
    echo "公网连接服务已停止。"
  fi
}

health_ok() {
  curl -4 -fsS --connect-timeout 5 --max-time 8 "$1" 2>/dev/null \
    | /usr/bin/grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'
}

restart_backend() {
  local deadline
  pkill -TERM -f '[u]vicorn backend.main:app' 2>/dev/null || true
  deadline=$((SECONDS + 10))
  while pgrep -f '[u]vicorn backend.main:app' >/dev/null; do
    [[ $SECONDS -lt $deadline ]] || return 1
    sleep 1
  done
  "$PROJECT_DIR/.venv/bin/python" - "$PROJECT_DIR" "$BACKEND_LOG" <<'PY' || return 1
import subprocess
import sys
from pathlib import Path

project = Path(sys.argv[1])
with open(sys.argv[2], "w") as log:
    subprocess.Popen(
        [str(project / "scripts" / "mac" / "start_backend_mac.sh")], cwd=project,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
PY
  deadline=$((SECONDS + 15))
  while [[ $SECONDS -lt $deadline ]]; do
    if health_ok http://127.0.0.1:8000/health; then
      echo "后端接口检查通过。"
      return 0
    fi
    sleep 1
  done
  return 1
}

finish() {
  local status=$? restored=1 message
  trap - EXIT INT TERM
  if [[ $status -ne 0 && -n "$BACKUP_DIR" ]]; then
    echo "切换未完成，正在恢复原模式……" >&2
    if [[ $CONFIG_CHANGED -eq 1 ]]; then
      cp -p "$BACKUP_DIR/backend.env" "$ENV_FILE" || restored=0
      cp -p "$BACKUP_DIR/mini-env.js" "$MINI_ENV_FILE" || restored=0
      cp -p "$BACKUP_DIR/login.js" "$LOGIN_FILE" || restored=0
      if [[ $BACKEND_WAS_RUNNING -eq 1 ]]; then
        restart_backend || restored=0
      else
        pkill -TERM -f '[u]vicorn backend.main:app' 2>/dev/null || true
      fi
    fi
    if [[ $TUNNEL_WAS_LOADED -eq 1 ]]; then
      tunnel_loaded || set_tunnel start || restored=0
    elif tunnel_loaded; then
      set_tunnel stop || restored=0
    fi
  fi
  if [[ $status -eq 0 ]]; then
    message="已切换到${MODE_NAME}。${NEXT_STEP}"
  elif [[ $restored -eq 1 ]]; then
    message="${LAST_ERROR} 未完成切换，原配置和服务状态已保留或恢复。"
  else
    message="${LAST_ERROR} 原配置已尝试恢复，但服务恢复未成功。请查看终端输出和 backend_mac.log。"
  fi
  if [[ $restored -eq 0 ]]; then
    message="${message} 配置备份保留在：${BACKUP_DIR}"
  elif [[ -n "$BACKUP_DIR" ]]; then
    rm -rf "$BACKUP_DIR"
  fi
  echo "$message"
  if [[ $GUI -eq 1 ]]; then show_message "$message" || true; fi
  exit "$status"
}

MODE="${1:-}"
if [[ -z "$MODE" ]]; then
  GUI=1
  MODE="$(/usr/bin/osascript <<'APPLESCRIPT'
set choices to {"线上调测：本机接口＋测试账号", "线下测试：公网接口＋真实微信"}
set selectedMode to choose from list choices with title "资产盘点 · 模式切换" with prompt "请选择运行模式：" default items {item 1 of choices} OK button name "切换" cancel button name "取消"
if selectedMode is false then return "cancel"
if item 1 of selectedMode is item 1 of choices then return "online"
return "field"
APPLESCRIPT
  )" || exit 1
  [[ "$MODE" != "cancel" ]] || exit 0
fi

trap finish EXIT
trap 'LAST_ERROR="操作已中断。"; exit 130' INT TERM
case "$MODE" in
  online)
    MODE_NAME="线上调测模式"
    TARGET_API="$LOCAL_API"
    DEV_LOGIN=true
    DEV_FLAG=1
    REQUEST_FLAG=0
    NEXT_STEP="请重新编译小程序，退出原账号后选择测试账号。"
    ;;
  field)
    MODE_NAME="线下测试模式"
    TARGET_API="$PUBLIC_API"
    DEV_LOGIN=false
    DEV_FLAG=0
    REQUEST_FLAG=1
    NEXT_STEP="请重新编译小程序并退出测试账号，使用微信登录；手机体验版需重新上传。"
    ;;
  *) fail "用法：$0 [online|field]" ;;
esac

for file in "$ENV_FILE" "$MINI_ENV_FILE" "$LOGIN_FILE"; do
  [[ -f "$file" ]] || fail "缺少配置文件：$file"
done
[[ -x "$PROJECT_DIR/.venv/bin/python" && -x "$SCRIPT_DIR/start_backend_mac.sh" ]] || fail "后端启动环境不完整，请先运行 scripts/mac/setup_mac.sh。"
/usr/bin/grep -Eq '^  devApiBase: ".*",$' "$MINI_ENV_FILE" || fail "小程序接口配置格式不正确。"
/usr/bin/grep -Eq '^const ENABLE_DEV_LOGIN = (true|false);$' "$LOGIN_FILE" || fail "小程序登录开关格式不正确。"
for key in INVENTORY_DEV_LOGIN INVENTORY_ALLOW_LOGIN_REQUESTS; do
  /usr/bin/grep -Eq "^${key}=[01]$" "$ENV_FILE" || fail "后端缺少有效配置：$key"
done
if tunnel_loaded; then TUNNEL_WAS_LOADED=1; fi
if pgrep -f '[u]vicorn backend.main:app' >/dev/null; then BACKEND_WAS_RUNNING=1; fi
if [[ "$MODE" == "field" || $TUNNEL_WAS_LOADED -eq 1 ]]; then
  [[ -f "$TUNNEL_PLIST" ]] || fail "未找到已安装的公网连接服务。"
fi

umask 077
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/inventory-mode.XXXXXX")"
cp -p "$ENV_FILE" "$BACKUP_DIR/backend.env"
cp -p "$MINI_ENV_FILE" "$BACKUP_DIR/mini-env.js"
cp -p "$LOGIN_FILE" "$BACKUP_DIR/login.js"

echo "[1/4] 切换公网连接服务；如弹出系统授权框，请输入 Mac 管理员密码。"
if [[ "$MODE" == "field" ]]; then
  set_tunnel start || fail "公网连接启动失败或系统授权已取消。"
else
  set_tunnel stop || fail "公网连接停止失败或系统授权已取消。"
fi

echo "[2/4] 更新小程序和后端配置……"
CONFIG_CHANGED=1
/usr/bin/sed -i '' -E "s#^  devApiBase: \".*\",\$#  devApiBase: \"$TARGET_API\",#" "$MINI_ENV_FILE"
/usr/bin/sed -i '' -E "s#^const ENABLE_DEV_LOGIN = (true|false);\$#const ENABLE_DEV_LOGIN = $DEV_LOGIN;#" "$LOGIN_FILE"
/usr/bin/sed -i '' -E "s#^INVENTORY_DEV_LOGIN=[01]\$#INVENTORY_DEV_LOGIN=$DEV_FLAG#" "$ENV_FILE"
/usr/bin/sed -i '' -E "s#^INVENTORY_ALLOW_LOGIN_REQUESTS=[01]\$#INVENTORY_ALLOW_LOGIN_REQUESTS=$REQUEST_FLAG#" "$ENV_FILE"

echo "[3/4] 重启后端并检查本机接口……"
restart_backend || fail "后端启动失败，请查看 backend_mac.log。"
echo "[4/4] 检查小程序连接……"
if [[ "$MODE" == "field" ]]; then
  echo "等待公网接口就绪（最多约 30 秒）……"
  DEADLINE=$((SECONDS + 30))
  until health_ok "${PUBLIC_API%/api}/health"; do
    [[ $SECONDS -lt $DEADLINE ]] || fail "公网接口检查未通过，请检查网络后重试。"
    sleep 1
  done
  echo "公网接口检查通过。"
elif tunnel_loaded; then
  fail "公网连接服务未能关闭。"
fi
