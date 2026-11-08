# 【Windows 专用】兼容旧名称的正式后端入口，转发到 start_backend_prod.ps1。
# 只启动后端，不会启动 Cloudflare Tunnel；Mac 日常运行无需使用。
# 保留 Windows 支持时需与 start_backend_prod.ps1 成套保留。
& (Join-Path $PSScriptRoot "start_backend_prod.ps1")
