# 【Windows 专用】读取根目录 .env，启动 local_agent.app 的本机 127.0.0.1:9000 服务。
# 由 start_local_agent.bat 调用；只提供 AI 辅助分析，不启动业务后端或管理页面。
# 仅本地 AI 功能需要，Mac 使用 start_agent_mac.sh。
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $projectRoot

$envFile = Join-Path $projectRoot ".env"
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
python -m uvicorn local_agent.app:app --host 127.0.0.1 --port 9000
pause
