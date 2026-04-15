param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$DocPath,
    [Parameter(Position=1)]
    [string]$DocType = "",
    [Parameter(Position=2)]
    [string]$ProjectPath = "."
)

$ErrorActionPreference = "Stop"

$DQG_DIR = Split-Path -Parent $PSScriptRoot
$VENV = Join-Path $DQG_DIR ".venv\Scripts\Activate.ps1"
$PROXY_URL = "http://localhost:4000"

if (-not (Test-Path $DocPath)) {
    Write-Host "[dqg] ERROR: Document not found: $DocPath" -ForegroundColor Red
    exit 1
}

& $VENV

# Check proxy
$proxyReady = $false
try {
    $resp = Invoke-WebRequest -Uri "$PROXY_URL/health" -Headers @{Authorization="Bearer sk-dqg-local"} -TimeoutSec 5 -UseBasicParsing
    $json = $resp.Content | ConvertFrom-Json
    if ($json.healthy_count -gt 0) { $proxyReady = $true }
} catch {}

if (-not $proxyReady) {
    Write-Host "[dqg] LiteLLM proxy not running. Starting..." -ForegroundColor Yellow
    Start-Process -FilePath "litellm" -ArgumentList "--config","$DQG_DIR\config\litellm\config.yaml","--port","4000" -NoNewWindow -RedirectStandardOutput "$env:TEMP\litellm_proxy.log" -RedirectStandardError "$env:TEMP\litellm_proxy_err.log"

    Write-Host "[dqg] Waiting for proxy..." -ForegroundColor Green
    for ($i = 1; $i -le 30; $i++) {
        Start-Sleep -Seconds 2
        try {
            $resp = Invoke-WebRequest -Uri "$PROXY_URL/health" -Headers @{Authorization="Bearer sk-dqg-local"} -TimeoutSec 5 -UseBasicParsing
            $json = $resp.Content | ConvertFrom-Json
            if ($json.healthy_count -gt 0) {
                Write-Host "[dqg] Proxy ready." -ForegroundColor Green
                break
            }
        } catch {}
        if ($i -eq 30) {
            Write-Host "[dqg] ERROR: Proxy failed to start." -ForegroundColor Red
            exit 1
        }
    }
}

# Build command
$cmd = "python -m app.cli review `"$DocPath`""
if ($DocType) { $cmd += " -t $DocType" }
if ($ProjectPath) { $cmd += " --project `"$ProjectPath`"" }

Write-Host "[dqg] Running: $cmd" -ForegroundColor Green
Set-Location $DQG_DIR
Invoke-Expression $cmd

# Show report
$runsDir = Join-Path $DQG_DIR "outputs\runs"
if (Test-Path $runsDir) {
    $latestRun = Get-ChildItem $runsDir -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latestRun) {
        $reportPath = Join-Path $latestRun.FullName "report.md"
        if (Test-Path $reportPath) {
            Write-Host ""
            Write-Host "[dqg] Report: $reportPath" -ForegroundColor Green
            Get-Content $reportPath -Head 40
        }
    }
}
