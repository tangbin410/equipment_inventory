@echo off
REM 【Windows 专用】本地 AI Agent 启动入口，调用 start_local_agent.ps1。
REM 服务在本机 9000 端口运行；仅使用本地 AI 分析时需要。
REM Mac 使用 start_agent_mac.sh；保留 Windows 支持时需与同名 .ps1 成套保留。
chcp 65001 >nul
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local_agent.ps1"
