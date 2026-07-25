param(
    [ValidateSet("dev", "trial")]
    [string]$Mode
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Content
    )
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Select-Mode {
    Write-Host ""
    Write-Host "Select mini program mode:"
    Write-Host "1. DEV mode: WeChat DevTools + test users"
    Write-Host "2. TRIAL mode: experience version + real WeChat login"
    $choice = Read-Host "Enter 1 or 2"
    switch ($choice.Trim()) {
        "1" { return "dev" }
        "2" { return "trial" }
        default { throw "Invalid choice. Enter 1 or 2." }
    }
}

if (-not $Mode) {
    $Mode = Select-Mode
}

$isDev = $Mode -eq "dev"
$loginPath = Join-Path $root "miniprogram\pages\login\login.js"
$backendBatPath = Join-Path $root "start_backend.bat"

$loginText = Get-Content -Raw -Encoding UTF8 -Path $loginPath
if ($loginText -notmatch "const ENABLE_DEV_LOGIN = (true|false);") {
    throw "Login dev switch not found: $loginPath"
}
$loginText = $loginText -replace "const ENABLE_DEV_LOGIN = (true|false);", ("const ENABLE_DEV_LOGIN = " + $isDev.ToString().ToLower() + ";")
Write-Utf8NoBom -Path $loginPath -Content $loginText

$backendText = Get-Content -Raw -Encoding UTF8 -Path $backendBatPath
if ($backendText -notmatch "set INVENTORY_DEV_LOGIN=\d") {
    throw "Backend dev-login switch not found: $backendBatPath"
}

if ($isDev) {
    $backendText = $backendText -replace "set INVENTORY_DEV_LOGIN=\d", "set INVENTORY_DEV_LOGIN=1"
    $backendText = $backendText -replace "\r?\nset /p WECHAT_SECRET=.*", ""
    $backendText = $backendText -replace "echo Current mode: .*", "echo Current mode: DEV, test users worker_001 / worker_002 / worker_005 enabled"
    $backendText = $backendText -replace "echo 当前模式: .*", "echo Current mode: DEV, test users worker_001 / worker_002 / worker_005 enabled"
    if ($backendText -notmatch "echo Current mode:") {
        $backendText = $backendText -replace "(set WECHAT_APPID=.*\r?\n)", "`$1echo Current mode: DEV, test users worker_001 / worker_002 / worker_005 enabled`r`n"
    }
} else {
    $backendText = $backendText -replace "set INVENTORY_DEV_LOGIN=\d", "set INVENTORY_DEV_LOGIN=0"
    if ($backendText -notmatch "set /p WECHAT_SECRET=") {
        $backendText = $backendText -replace "(set WECHAT_APPID=.*\r?\n)", "`$1set /p WECHAT_SECRET=Enter WeChat AppSecret:`r`n"
    }
    $backendText = $backendText -replace "echo Current mode: .*", "echo Current mode: TRIAL, real WeChat login; keep Cloudflare tunnel running"
    $backendText = $backendText -replace "echo 当前模式: .*", "echo Current mode: TRIAL, real WeChat login; keep Cloudflare tunnel running"
    if ($backendText -notmatch "echo Current mode:") {
        $backendText = $backendText -replace "(set /p WECHAT_SECRET=.*\r?\n)", "`$1echo Current mode: TRIAL, real WeChat login; keep Cloudflare tunnel running`r`n"
    }
}
Write-Utf8NoBom -Path $backendBatPath -Content $backendText

Write-Host ""
if ($isDev) {
    Write-Host "Switched to DEV mode."
    Write-Host "- Test users are visible in WeChat DevTools."
    Write-Host "- Backend allows worker_001 / worker_002 / worker_005."
    Write-Host "- Restart start_backend.bat to apply backend mode."
} else {
    Write-Host "Switched to TRIAL mode."
    Write-Host "- Test users are hidden."
    Write-Host "- Backend uses real WeChat login and asks for AppSecret."
    Write-Host "- Restart start_backend.bat and upload a new trial version."
}
Write-Host ""
Write-Host "Remember: restart backend and rebuild in WeChat DevTools after switching."
