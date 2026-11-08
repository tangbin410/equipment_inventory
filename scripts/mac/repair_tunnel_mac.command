#!/usr/bin/env bash

# 用途：将已安装的 Cloudflare 隧道切换为 HTTP/2（TCP），重启并验证公网接口。
# 使用：公网连接异常时的维修备用工具；需要 Mac 管理员授权，平时无需运行。
# 影响：备份系统隧道配置后修改并重启；小程序与异地后台会短暂断开，失败时尝试恢复。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "$0")"
if [[ $EUID -ne 0 ]]; then
  exec /usr/bin/osascript - "$SCRIPT_PATH" <<'APPLESCRIPT'
on run argv
  do shell script "/bin/bash " & quoted form of (item 1 of argv) with administrator privileges with prompt "修复小程序连接：将 Cloudflare 隧道切换为 HTTP/2（TCP），并重启隧道服务。"
end run
APPLESCRIPT
fi

PLIST="/Library/LaunchDaemons/com.cloudflare.cloudflared.plist"
BACKUP_DIR="/Library/Application Support/com.cloudflare.cloudflared/backups"
PLIST_BUDDY="/usr/libexec/PlistBuddy"
LAUNCHCTL="/bin/launchctl"
SERVICE="system/com.cloudflare.cloudflared"
LOG="/Library/Logs/com.cloudflare.cloudflared.err.log"

umask 077
mkdir -p "$BACKUP_DIR"
BACKUP="$(mktemp "$BACKUP_DIR/before-http2.XXXXXX")"
CANDIDATE="$(mktemp "$BACKUP_DIR/http2-candidate.XXXXXX")"
cp -p "$PLIST" "$BACKUP"
cp -p "$PLIST" "$CANDIDATE"
trap 'rm -f "$CANDIDATE"' EXIT

if ! "$PLIST_BUDDY" -c 'Print :EnvironmentVariables' "$CANDIDATE" >/dev/null 2>&1; then
  "$PLIST_BUDDY" -c 'Add :EnvironmentVariables dict' "$CANDIDATE"
fi
if ! "$PLIST_BUDDY" -c 'Set :EnvironmentVariables:TUNNEL_TRANSPORT_PROTOCOL http2' "$CANDIDATE" >/dev/null 2>&1; then
  "$PLIST_BUDDY" -c 'Add :EnvironmentVariables:TUNNEL_TRANSPORT_PROTOCOL string http2' "$CANDIDATE"
fi
/usr/bin/plutil -lint "$CANDIDATE" >/dev/null

stop_tunnel() {
  local service_pid
  local stop_deadline
  service_pid="$("$LAUNCHCTL" print "$SERVICE" 2>/dev/null | /usr/bin/awk '/^[[:space:]]*pid =/ { print $3 }')" || return 0
  "$LAUNCHCTL" bootout "$SERVICE" || return 1
  stop_deadline=$((SECONDS + 35))
  while [[ -n "$service_pid" ]] && kill -0 "$service_pid" 2>/dev/null; do
    if [[ $SECONDS -ge $stop_deadline ]]; then
      return 1
    fi
    sleep 1
  done
}

restore_original() {
  trap - ERR
  cp -p "$BACKUP" "$PLIST"
  if ! stop_tunnel || ! "$LAUNCHCTL" bootstrap system "$PLIST"; then
    echo "原配置已恢复，但隧道重启失败，请检查系统服务。备份：$BACKUP" >&2
    return 1
  fi
  echo "连接验证未通过，已恢复原隧道配置。备份：$BACKUP"
}
LOG_POSITION="$(/usr/bin/stat -f %z "$LOG")"
trap 'restore_original; exit 1' ERR

cp -p "$CANDIDATE" "$PLIST"
stop_tunnel
"$LAUNCHCTL" bootstrap system "$PLIST"

DEADLINE=$((SECONDS + 40))
while [[ $SECONDS -lt $DEADLINE ]]; do
  if /usr/bin/tail -c "+$((LOG_POSITION + 1))" "$LOG" | /usr/bin/grep 'Registered tunnel connection.*protocol=http2' >/dev/null \
    && /usr/bin/curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:20241/ready >/dev/null 2>&1 \
    && /usr/bin/curl --noproxy '*' -fsS --connect-timeout 2 --max-time 4 https://api.equip-inventory.xyz/health >/dev/null 2>&1; then
    trap - ERR
    echo "已切换 HTTP/2（TCP），隧道与公网接口检查通过。原配置备份：$BACKUP"
    exit 0
  fi
  sleep 2
done

restore_original
exit 1
