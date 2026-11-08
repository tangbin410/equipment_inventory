@echo off
REM 【Windows 专用】兼容旧文件名的后端启动入口，实际转发至 start_backend_dev.bat。
REM 默认启动本机开发测试后端，不是公网现场模式；Mac 日常运行无需使用。
REM 保留 Windows 支持时需与 start_backend_dev.bat 成套保留。
call "%~dp0start_backend_dev.bat"
