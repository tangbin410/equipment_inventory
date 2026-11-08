@echo off
REM 【Windows 专用】启用本地 AI Agent 的配置入口；Mac 日常运行无需使用。
REM 调用 enable_local_agent.ps1，更新 .env 中的 Agent 地址并按需生成调用令牌。
REM 不会启动服务；后续需启动 start_local_agent.bat 并重启后端。
chcp 65001 >nul
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_local_agent.ps1"
