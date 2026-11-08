@echo off
REM 【Windows 专用】启动本机 Vue 管理端开发服务，通常访问 127.0.0.1:5173。
REM 缺少前端依赖时执行 npm install；发现 5173 正在监听时直接退出。
REM Mac 使用 start_admin_mac.sh 或 start_all_mac.command；此脚本不发布远程正式后台。
setlocal
chcp 65001 >nul

echo ==============================
echo Equipment Inventory - Vue Admin
echo ==============================
echo.

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found. Please install Node.js or add it to PATH.
  echo.
  pause
  exit /b 1
)

if not exist "%~dp0..\..\admin_web\package.json" (
  echo [ERROR] admin_web\package.json was not found.
  echo Please run this script from the project root folder.
  echo Script folder: %~dp0
  echo.
  pause
  exit /b 1
)

netstat -ano | findstr ":5173" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo Vue admin is already running.
  echo URL: http://127.0.0.1:5173
  echo.
  pause
  exit /b 0
)

cd /d "%~dp0..\..\admin_web"
if not exist "node_modules\.bin\vite.cmd" (
  echo Installing frontend dependencies...
  npm install
  if errorlevel 1 (
    echo.
    echo [ERROR] npm install failed. Please check the message above.
    pause
    exit /b 1
  )
  echo.
)

echo Starting Vue3 + Element Plus admin...
echo URL: http://127.0.0.1:5173
echo.
npm run dev
if errorlevel 1 (
  echo.
  echo [ERROR] Vue admin failed to start. Please send me the message above.
)
echo.
pause
