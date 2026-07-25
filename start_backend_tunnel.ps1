Set-Location -LiteralPath $PSScriptRoot

$env:INVENTORY_SECRET_KEY = "equip-inventory-local-secret-key-please-change-2026"
$env:INVENTORY_ADMIN_PASSWORD = "admin_001"
$env:INVENTORY_DEV_LOGIN = "0"
$env:INVENTORY_ALLOW_LOGIN_REQUESTS = "1"
$env:WECHAT_APPID = "wxf7024f174bbc84fd"

$env:WECHAT_SECRET = Read-Host "Enter WeChat AppSecret"

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
