#!/usr/bin/env bash

# 用途：本机交互恢复现有启用管理员的密码，默认登录编码 admin_001。
# 影响：先备份数据库，再更新密码、使旧登录失效并记审计；不改角色、账号状态或 .env。
# 使用：双击后由本人隐藏输入新密码；不接受参数/管道，不启动、停止服务或切换模式。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || exit 1

if [[ $# -ne 0 || ! -t 0 || ! -t 1 || ! -t 2 ]]; then
  echo "请直接双击或在交互终端运行本工具；不接受参数、管道或重定向。" >&2
  exit 2
fi

status=1
if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "缺少项目 Python 环境，请先完成安装。" >&2
else
  "$PROJECT_DIR/.venv/bin/python" -m backend.admin_recovery
  status=$?
fi

read -r -p "按回车键关闭窗口……" _reply || true
exit "$status"
