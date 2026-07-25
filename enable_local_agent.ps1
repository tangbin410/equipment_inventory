param(
    [switch]$NoPause
)

Set-Location -LiteralPath $PSScriptRoot

$envPath = Join-Path $PSScriptRoot ".env"
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
Set-Content -LiteralPath $envPath -Value $updated -Encoding UTF8

Write-Host "INVENTORY_AI_AGENT_URL has been set to $agentUrl" -ForegroundColor Green
Write-Host "Next: start start_local_agent.bat, then restart the backend."
if (-not $NoPause) {
    pause
}
