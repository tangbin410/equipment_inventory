@echo off
chcp 65001 >nul
echo ==============================
echo 机房资产盘点系统 - 启动后端
echo ==============================
echo.
cd /d "%~dp0"
rem 临时模式开关：由 switch_miniprogram_mode.ps1 自动切换。
set WECHAT_APPID=wxf7024f174bbc84fd
if exist ".env" (
    echo 正在读取本地配置: .env
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
) else (
    echo 未找到 .env，请先按 .env.example 创建本地配置。
)
if not defined INVENTORY_SECRET_KEY (
    echo 缺少 INVENTORY_SECRET_KEY，请在 .env 中配置至少32位随机字符串。
    pause
    exit /b 1
)
if not defined INVENTORY_DEV_LOGIN set INVENTORY_DEV_LOGIN=0
if defined DEEPSEEK_API_KEY (echo AI配置: DEEPSEEK_API_KEY 已加载) else (echo AI配置: 未加载 DEEPSEEK_API_KEY)
if defined DEEPSEEK_MODEL (echo AI配置: DEEPSEEK_MODEL=%DEEPSEEK_MODEL%) else (echo AI配置: 未加载 DEEPSEEK_MODEL)
echo 当前模式: INVENTORY_DEV_LOGIN=%INVENTORY_DEV_LOGIN%
echo 正在启动 FastAPI 后端...
echo 地址: http://127.0.0.1:8000
echo 文档: http://127.0.0.1:8000/docs
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
echo.
pause
