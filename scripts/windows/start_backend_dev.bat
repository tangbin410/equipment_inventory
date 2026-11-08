@echo off
REM 【Windows 专用】读取根目录 .env，启动本机 127.0.0.1:8000 开发后端并启用热重载。
REM 由 start_backend.bat 调用，强制启用 worker 测试账号；检测到 cloudflared 运行会拒绝启动。
REM 仅用于本机开发测试；Mac 使用现有 Mac 启动或模式切换脚本。
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."

echo ==============================
echo Equipment Inventory - DEV backend
echo ==============================
echo.
echo DEV mode is for local WeChat DevTools/browser testing only.
echo It enables worker_001 / worker_002 style test login.
echo.

tasklist 2>nul | find /I "cloudflared" >nul
if not errorlevel 1 (
  echo [BLOCKED] cloudflared is running.
  echo Stop Cloudflare Tunnel before starting DEV mode, otherwise test login may be exposed to the public internet.
  echo.
  pause
  exit /b 1
)

if exist ".env" (
  echo Loading local config: .env
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
  )
) else (
  echo [ERROR] .env not found. Create it from .env.example first.
  echo.
  pause
  exit /b 1
)

if not defined INVENTORY_SECRET_KEY (
  echo [ERROR] INVENTORY_SECRET_KEY is missing in .env.
  echo.
  pause
  exit /b 1
)

set INVENTORY_DEV_LOGIN=1
set INVENTORY_ALLOW_LOGIN_REQUESTS=1
set INVENTORY_ENV=development

echo Mode: DEV
echo API:  http://127.0.0.1:8000
echo Docs: http://127.0.0.1:8000/docs
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
echo.
pause
