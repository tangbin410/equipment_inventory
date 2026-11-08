@echo off
REM 【Windows 专用】AI 参数配置入口；Mac 日常运行无需使用。
REM 调用 configure_ai_env.ps1，交互填写模型、接口地址和密钥并更新项目根目录 .env。
REM 需与同名 .ps1 一起保留；不会启动后端，配置后需另行重启。
chcp 65001 >nul
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_ai_env.ps1"
