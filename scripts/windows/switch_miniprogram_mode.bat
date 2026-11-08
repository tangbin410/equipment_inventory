@echo off
REM 【Windows 历史入口】调用 switch_miniprogram_mode.ps1 修改旧版小程序和后端开关。
REM 对应 .ps1 依赖旧版启动文件内容，当前项目不建议使用，可能只修改部分配置后报错。
REM Mac 请双击 switch_mode_mac.command；此入口不会可靠管理现有隧道和服务。
chcp 65001 >nul
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0switch_miniprogram_mode.ps1"
echo.
pause
