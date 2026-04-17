$ErrorActionPreference = "Stop"

$tag = "dqg"

Write-Host ""
Write-Host "  =========================================================" -ForegroundColor Cyan
Write-Host "     Doc Quality Gate - Stopping" -ForegroundColor Cyan
Write-Host "  =========================================================" -ForegroundColor Cyan
Write-Host ""

# -- Stop Web UI (port 8080) --

Write-Host "($tag) Stopping Web UI (port 8080)..." -ForegroundColor Green
$webProcs = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmdLine -match "app.cli web"
    } catch { $false }
}
if ($webProcs) {
    $webProcs | Stop-Process -Force
    Write-Host "($tag) Web UI stopped" -ForegroundColor Green
} else {
    Write-Host "($tag) Web UI not running" -ForegroundColor Yellow
}

# -- Stop cmd wrappers for Web UI --

$webCmdProcs = Get-Process -Name cmd -ErrorAction SilentlyContinue | Where-Object {
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmdLine -match "dqg-start-web"
    } catch { $false }
}
if ($webCmdProcs) {
    $webCmdProcs | Stop-Process -Force
}

# -- Stop LiteLLM proxy (port 4000) --

Write-Host "($tag) Stopping LiteLLM proxy (port 4000)..." -ForegroundColor Green
$litellmProcs = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmdLine -match "litellm"
    } catch { $false }
}
if ($litellmProcs) {
    $litellmProcs | Stop-Process -Force
    Write-Host "($tag) LiteLLM proxy stopped" -ForegroundColor Green
} else {
    Write-Host "($tag) LiteLLM proxy not running" -ForegroundColor Yellow
}

# -- Stop cmd wrappers for proxy --

$proxyCmdProcs = Get-Process -Name cmd -ErrorAction SilentlyContinue | Where-Object {
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmdLine -match "dqg-start-proxy"
    } catch { $false }
}
if ($proxyCmdProcs) {
    $proxyCmdProcs | Stop-Process -Force
}

# -- Cleanup temp bat files --

@("$env:TEMP\dqg-start-web.bat", "$env:TEMP\dqg-start-proxy.bat") | ForEach-Object {
    if (Test-Path $_) { Remove-Item $_ -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host "  All services stopped." -ForegroundColor Green
Write-Host ""
