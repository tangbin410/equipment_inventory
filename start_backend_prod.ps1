$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "=============================="
Write-Host "Equipment Inventory - PROD backend"
Write-Host "=============================="
Write-Host ""
Write-Host "PROD mode disables worker_001 style test login and is safe to use behind Cloudflare Tunnel."
Write-Host ""

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw ".env not found. Create it from .env.example and fill production secrets first."
}

$loadedNames = @{}
foreach ($name in @(
    "INVENTORY_SECRET_KEY",
    "INVENTORY_ADMIN_PASSWORD",
    "INVENTORY_DEV_LOGIN",
    "INVENTORY_ALLOW_LOGIN_REQUESTS",
    "WECHAT_APPID",
    "WECHAT_SECRET"
)) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}

Get-Content -LiteralPath $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
        return
    }
    $name, $value = $line.Split("=", 2)
    $cleanName = $name.Trim()
    [Environment]::SetEnvironmentVariable($cleanName, $value.Trim().Trim('"').Trim("'"), "Process")
    $loadedNames[$cleanName] = $true
}

if (-not $loadedNames.ContainsKey("INVENTORY_SECRET_KEY") -or -not $env:INVENTORY_SECRET_KEY -or $env:INVENTORY_SECRET_KEY.Length -lt 32) {
    throw "INVENTORY_SECRET_KEY must be set in .env and be at least 32 characters."
}

if ($env:INVENTORY_ADMIN_PASSWORD -eq "admin_001") {
    throw "INVENTORY_ADMIN_PASSWORD must not be the default value admin_001."
}

if (-not $loadedNames.ContainsKey("INVENTORY_ADMIN_PASSWORD") -or -not $env:INVENTORY_ADMIN_PASSWORD) {
    Write-Host "INVENTORY_ADMIN_PASSWORD is not set; using the existing admin password hash in the database."
}

$env:INVENTORY_DEV_LOGIN = "0"
$env:INVENTORY_ENV = "production"
if (-not $loadedNames.ContainsKey("INVENTORY_ALLOW_LOGIN_REQUESTS") -or -not $env:INVENTORY_ALLOW_LOGIN_REQUESTS) {
    $env:INVENTORY_ALLOW_LOGIN_REQUESTS = "0"
}

if (-not $loadedNames.ContainsKey("WECHAT_APPID") -or -not $loadedNames.ContainsKey("WECHAT_SECRET") -or -not $env:WECHAT_APPID -or -not $env:WECHAT_SECRET) {
    throw "WECHAT_APPID and WECHAT_SECRET must be set in .env for production WeChat login."
}

Write-Host "Mode: PROD"
Write-Host "INVENTORY_ENV=$env:INVENTORY_ENV"
Write-Host "INVENTORY_DEV_LOGIN=$env:INVENTORY_DEV_LOGIN"
Write-Host "INVENTORY_ALLOW_LOGIN_REQUESTS=$env:INVENTORY_ALLOW_LOGIN_REQUESTS"
Write-Host "API: http://127.0.0.1:8000"
Write-Host ""

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
