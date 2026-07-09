@echo off
chcp 65001 >nul
echo ==============================
echo 机房资产盘点系统 - 启动后端
echo ==============================
echo.
cd /d "%~dp0"
echo 正在启动 FastAPI 后端...
echo 地址: http://localhost:8000
echo 文档: http://localhost:8000/docs
echo.
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
echo.
pause
