Set-Location -LiteralPath $PSScriptRoot

$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path -LiteralPath $envFile) {
    Get-Content -LiteralPath $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $name, $value = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim().Trim('"').Trim("'"), "Process")
    }
}

Write-Host "Starting local inventory AI agent..."
Write-Host "URL: http://127.0.0.1:9000/analyze"
python -m uvicorn local_agent.app:app --host 127.0.0.1 --port 9000 --reload
pause

