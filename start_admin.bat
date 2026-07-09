@echo off
chcp 65001 >nul
echo ==============================
echo 机房资产盘点系统 - 启动PC后台
echo ==============================
echo.
cd /d "%~dp0"
echo 正在启动 Streamlit...
echo.
streamlit run pc_admin/admin.py
echo.
pause
