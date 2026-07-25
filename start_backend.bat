@echo off
chcp 65001 >nul
echo ==============================
echo 机房资产盘点系统 - 启动后端
echo ==============================
echo.
cd /d "%~dp0"
rem 临时模式开关：由 switch_miniprogram_mode.ps1 自动切换。
set INVENTORY_SECRET_KEY=equip-inventory-local-secret-key-please-change-2026
set INVENTORY_ADMIN_PASSWORD=admin_001
set INVENTORY_DEV_LOGIN=1
set INVENTORY_ALLOW_LOGIN_REQUESTS=1
set WECHAT_APPID=wxf7024f174bbc84fd
if exist ".env" (
    echo 正在读取本地配置: .env
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
) else (
    echo 未找到 .env，本次仅使用脚本内默认配置。
)
if defined DEEPSEEK_API_KEY (echo AI配置: DEEPSEEK_API_KEY 已加载) else (echo AI配置: 未加载 DEEPSEEK_API_KEY)
if defined DEEPSEEK_MODEL (echo AI配置: DEEPSEEK_MODEL=%DEEPSEEK_MODEL%) else (echo AI配置: 未加载 DEEPSEEK_MODEL)
echo Current mode: DEV, test users worker_001 / worker_002 / worker_005 enabled
echo 当前模式: 小程序开发调测，支持测试账号 worker_001 / worker_002 / worker_005
echo 正在启动 FastAPI 后端...
echo 地址: http://localhost:8000
echo 文档: http://localhost:8000/docs
echo.
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
echo.
pause
