# 【Windows 专用】将 .env 的 AI Agent 地址设为本机 9000 端口，缺少令牌时生成。
# 由 enable_local_agent.bat 调用，也支持 -NoPause；Mac 日常运行无需使用。
# 只修改配置，不启动 Agent 或重启后端；修改前应保留 .env 备份。
param(
    [switch]$NoPause
)

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $projectRoot

$envPath = Join-Path $projectRoot ".env"
$agentUrl = "http://127.0.0.1:9000/analyze"
$lines = @()
if (Test-Path -LiteralPath $envPath) {
    $lines = Get-Content -LiteralPath $envPath
}

$found = $false
$updated = foreach ($line in $lines) {
    if ($line -match "^\s*#?\s*INVENTORY_AI_AGENT_URL\s*=") {
        $found = $true
        "INVENTORY_AI_AGENT_URL=$agentUrl"
    } else {
        $line
    }
}
if (-not $found) {
    $updated += "INVENTORY_AI_AGENT_URL=$agentUrl"
}

$tokenFound = $false
foreach ($line in $updated) {
    if ($line -match "^\s*#?\s*INVENTORY_AGENT_TOKEN\s*=") {
        $tokenFound = $true
        break
    }
}
if (-not $tokenFound) {
    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".ToCharArray()
    $token = -join (1..48 | ForEach-Object { $chars | Get-Random })
    $updated += "INVENTORY_AGENT_TOKEN=$token"
}
Set-Content -LiteralPath $envPath -Value $updated -Encoding UTF8

Write-Host "INVENTORY_AI_AGENT_URL has been set to $agentUrl" -ForegroundColor Green
Write-Host "INVENTORY_AGENT_TOKEN is configured for backend -> local agent calls." -ForegroundColor Green
Write-Host "Next: start start_local_agent.bat, then restart the backend."
if (-not $NoPause) {
    pause
}
