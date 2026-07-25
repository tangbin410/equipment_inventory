Set-Location -LiteralPath $PSScriptRoot

$env:INVENTORY_SECRET_KEY = "equip-inventory-local-secret-key-please-change-2026"
$env:INVENTORY_ADMIN_PASSWORD = "admin_001"
$env:INVENTORY_DEV_LOGIN = "0"
$env:INVENTORY_ALLOW_LOGIN_REQUESTS = "1"
$env:WECHAT_APPID = "wxf7024f174bbc84fd"

$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path -LiteralPath $envFile) {
    Write-Host "正在读取本地配置: .env"
    Get-Content -LiteralPath $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $name, $value = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
    }
} else {
    Write-Host "未找到 .env，本次仅使用脚本内默认配置。"
}
if ($env:DEEPSEEK_API_KEY) {
    Write-Host "AI配置: DEEPSEEK_API_KEY 已加载"
} else {
    Write-Host "AI配置: 未加载 DEEPSEEK_API_KEY"
}
if ($env:DEEPSEEK_MODEL) {
    Write-Host "AI配置: DEEPSEEK_MODEL=$env:DEEPSEEK_MODEL"
} else {
    Write-Host "AI配置: 未加载 DEEPSEEK_MODEL"
}

$env:WECHAT_SECRET = Read-Host "Enter WeChat AppSecret"

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
