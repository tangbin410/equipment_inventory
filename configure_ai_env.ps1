Set-Location -LiteralPath $PSScriptRoot

function Read-SecretText($prompt) {
    $secure = Read-Host $prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Remove-WrappingQuotes($value) {
    return ($value -as [string]).Trim().Trim('"').Trim("'")
}

function Has-ControlChar($value) {
    foreach ($char in (($value -as [string]).ToCharArray())) {
        $code = [int][char]$char
        if ($code -lt 32 -or $code -eq 127) {
            return $true
        }
    }
    return $false
}

$apiKey = Remove-WrappingQuotes (Read-SecretText "Enter DeepSeek API Key. If Ctrl+V does not paste, right-click to paste")
$model = Remove-WrappingQuotes (Read-Host "Enter DeepSeek model name, for example deepseek-chat")
$baseUrl = Remove-WrappingQuotes (Read-Host "Enter DeepSeek API base URL, press Enter to use https://api.deepseek.com")

if ([string]::IsNullOrWhiteSpace($apiKey)) {
    Write-Host "DEEPSEEK_API_KEY is empty. Canceled." -ForegroundColor Yellow
    pause
    exit 1
}
if ($apiKey.Length -lt 10 -or (Has-ControlChar $apiKey)) {
    Write-Host "DEEPSEEK_API_KEY looks invalid. It may contain invisible characters. Please paste the real key again." -ForegroundColor Yellow
    pause
    exit 1
}
if ([string]::IsNullOrWhiteSpace($model)) {
    Write-Host "DEEPSEEK_MODEL is empty. Canceled." -ForegroundColor Yellow
    pause
    exit 1
}
if (Has-ControlChar $model) {
    Write-Host "DEEPSEEK_MODEL contains invisible characters. Canceled." -ForegroundColor Yellow
    pause
    exit 1
}
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    $baseUrl = "https://api.deepseek.com"
}
if ((Has-ControlChar $baseUrl) -or -not ($baseUrl.StartsWith("http://") -or $baseUrl.StartsWith("https://"))) {
    Write-Host "DEEPSEEK_BASE_URL is invalid. Canceled." -ForegroundColor Yellow
    pause
    exit 1
}

$lines = @(
    "# Local secrets for equipment_inventory. Do not commit this file.",
    "DEEPSEEK_API_KEY=$apiKey",
    "DEEPSEEK_MODEL=$model",
    "DEEPSEEK_BASE_URL=$baseUrl"
)

Set-Content -LiteralPath ".env" -Value $lines -Encoding ASCII

Write-Host ""
Write-Host ".env has been written." -ForegroundColor Green
Write-Host "Next: close the backend window, run start_backend.bat again, then click the model check button on the AI analysis page."
pause
