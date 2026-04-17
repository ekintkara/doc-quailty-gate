$ErrorActionPreference = "Stop"

$tag = "dqg"

Write-Host ""
Write-Host "  =========================================================" -ForegroundColor Cyan
Write-Host "     Doc Quality Gate - Starting" -ForegroundColor Cyan
Write-Host "  =========================================================" -ForegroundColor Cyan
Write-Host ""

$DQG_DIR = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $DQG_DIR

# -- Cleanup existing LiteLLM processes --

Write-Host "($tag) Cleaning up existing LiteLLM processes..." -ForegroundColor Green
$litellmProcs = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmdLine -match "litellm"
    } catch { $false }
}
if ($litellmProcs) {
    $litellmProcs | Stop-Process -Force
    Write-Host "($tag) Stopped existing LiteLLM processes" -ForegroundColor Yellow
    Start-Sleep -Seconds 2
} else {
    Write-Host "($tag) No existing LiteLLM processes found" -ForegroundColor Green
}

# -- Prerequisites --

Write-Host ""
Write-Host "($tag) Checking prerequisites..." -ForegroundColor Green

$pythonOk = $false
try {
    $pythonVer = python --version 2>&1
    $m = [regex]::Match("$pythonVer", "Python 3\.(\d+)")
    if ($m.Success) {
        $minor = [int]$m.Groups[1].Value
        $pythonOk = ($minor -ge 11)
    }
} catch {}

if (-not $pythonOk) {
    Write-Host "($tag) ERROR: Python 3.11+ is required." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
Write-Host "($tag) Python: $(python --version 2>&1) OK" -ForegroundColor Green

$nodeOk = $false
try { $nodeOk = (node --version 2>&1) -match "v" } catch {}
if (-not $nodeOk) {
    Write-Host "($tag) ERROR: Node.js 18+ is required." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
Write-Host "($tag) Node.js: $(node --version 2>&1) OK" -ForegroundColor Green

# -- Step 1: venv + deps --

Write-Host ""
Write-Host "($tag) [1/7] Creating virtual environment..." -ForegroundColor Green

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& ".venv\Scripts\Activate.ps1"

Write-Host "($tag) Installing Python dependencies..." -ForegroundColor Green
$null = cmd /c "pip install -e `".[dev]`" 2>&1" | Select-Object -Last 1

Write-Host "($tag) Installing LiteLLM proxy dependencies..." -ForegroundColor Green
$null = cmd /c "pip install `"litellm[proxy]`" 2>&1" | Select-Object -Last 1

if (-not (& ".venv\Scripts\pip.exe" show orjson 2>$null)) {
    Write-Host "($tag) Installing orjson..." -ForegroundColor Green
    $null = cmd /c ".venv\Scripts\pip.exe install orjson --no-build-isolation 2>&1" | Select-Object -Last 1
}

Write-Host "($tag) Dependencies installed OK" -ForegroundColor Green

# -- Step 2: .env --

Write-Host ""
Write-Host "($tag) [2/7] Configuring environment..." -ForegroundColor Green

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "($tag) .env created from .env.example" -ForegroundColor Yellow
    } else {
        New-Item -Path ".env" -ItemType File | Out-Null
        Write-Host "($tag) .env created -empty-" -ForegroundColor Yellow
    }
}

$envContent = Get-Content ".env" -Raw
if ($null -eq $envContent) { $envContent = "" }

$needsKey = ($envContent -notmatch "ZAI_API_KEY=\S+") -or ($envContent -match "ZAI_API_KEY=your_zai")
$needsMasterKey = ($envContent -notmatch "LITELLM_MASTER_KEY=\S+")

if ($needsKey -or $needsMasterKey) {
    Write-Host ""

    if ($needsKey) {
        Write-Host "($tag) Z.AI API key not set." -ForegroundColor Yellow
        Write-Host "  Get your key from: https://z.ai" -ForegroundColor Cyan
        $apiKey = Read-Host "  Enter your Z.AI API key -or press Enter to skip-"
        if ($apiKey) {
            $envContent = $envContent.TrimEnd() + "`nZAI_API_KEY=$apiKey`n"
            Write-Host "($tag) Z.AI API key saved OK" -ForegroundColor Green
        } else {
            Write-Host "($tag) Skipped. Edit .env manually later." -ForegroundColor Yellow
        }
    } else {
        Write-Host "($tag) Z.AI API key already configured OK" -ForegroundColor Green
    }

    if ($needsMasterKey) {
        Write-Host ""
        Write-Host "($tag) LiteLLM master key not set." -ForegroundColor Yellow
        Write-Host "  This is required for the proxy to start." -ForegroundColor Cyan
        $masterKey = Read-Host "  Enter a master key -or press Enter for auto-generated-"
        if (-not $masterKey) { $masterKey = [guid]::NewGuid().ToString() }
        $envContent = $envContent.TrimEnd() + "`nLITELLM_MASTER_KEY=$masterKey`n"
        Write-Host "($tag) LiteLLM master key saved OK" -ForegroundColor Green
    }

    Set-Content ".env" $envContent
} else {
    Write-Host "($tag) Z.AI API key OK" -ForegroundColor Green
    Write-Host "($tag) LiteLLM master key OK" -ForegroundColor Green
}

# -- Step 3: Promptfoo --

Write-Host ""
Write-Host "($tag) [3/7] Checking Promptfoo..." -ForegroundColor Green
$promptfooOk = $false
try {
    $pfCheck = cmd /c "npx promptfoo --version 2>&1"
    if ($LASTEXITCODE -eq 0) {
        $promptfooOk = $true
        Write-Host "($tag) Promptfoo: $pfCheck OK" -ForegroundColor Green
    }
} catch {}

if (-not $promptfooOk) {
    Write-Host "($tag) Installing Promptfoo globally..." -ForegroundColor Yellow
    $null = cmd /c "npm install -g promptfoo 2>&1" | Select-Object -Last 1
    Start-Sleep -Seconds 2
    try {
        $pfVer2 = cmd /c "npx promptfoo --version 2>&1"
        Write-Host "($tag) Promptfoo: $pfVer2 OK" -ForegroundColor Green
    } catch {
        Write-Host "($tag) Promptfoo install skipped - will use npx on demand" -ForegroundColor Yellow
    }
}

# -- Step 4: opencode integration --

Write-Host ""
Write-Host "($tag) [4/7] Setting up opencode integration..." -ForegroundColor Green
$commandsDir = Join-Path $env:USERPROFILE ".config\opencode\commands"
New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null

$slashCmd = Join-Path $DQG_DIR ".opencode\commands\dqg.md"
if (Test-Path $slashCmd) {
    Copy-Item $slashCmd (Join-Path $commandsDir "dqg.md") -Force
    Write-Host "($tag) Slash command /dqg OK" -ForegroundColor Green
}

$opencodeDir = Join-Path $env:USERPROFILE ".config\opencode"
New-Item -ItemType Directory -Path $opencodeDir -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $opencodeDir "dqg_home"), $DQG_DIR, [System.Text.UTF8Encoding]::new($false))
Write-Host "($tag) DQG home path saved OK" -ForegroundColor Green

# -- Step 5: Verify Python modules --

Write-Host ""
Write-Host "($tag) [5/7] Verifying Python modules..." -ForegroundColor Green
$modErrors = 0

$checks = @(
    @{ Name = "Config"; Code = "from app.config import load_app_config; load_app_config()" },
    @{ Name = "Codebase scanner"; Code = "from app.stages.codebase_context import scan_project" },
    @{ Name = "Cross-reference"; Code = "from app.stages.cross_reference import run_cross_reference" },
    @{ Name = "LiteLLM client"; Code = "from app.integrations.litellm_client import LiteLLMClient" },
    @{ Name = "Orchestrator"; Code = "from app.orchestrator import Orchestrator" },
    @{ Name = "LiteLLM proxy"; Code = "import litellm.proxy; print('ok')" }
)

foreach ($chk in $checks) {
    $modOk = $false
    try { python -c $chk.Code 2>$null; $modOk = $true } catch {}
    if ($modOk) {
        Write-Host "  [OK] $($chk.Name)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($chk.Name)" -ForegroundColor Red
        $modErrors++
    }
}

# -- Step 6: Start LiteLLM proxy --

Write-Host ""
Write-Host "($tag) [6/7] Starting LiteLLM proxy on port 4000..." -ForegroundColor Green
$litellmConfig = Join-Path $DQG_DIR "config\litellm\config.yaml"
$LITELLM_LOG = Join-Path $env:TEMP "dqg-litellm.log"

$alreadyRunning = $false
try { $alreadyRunning = (Invoke-WebRequest -Uri "http://localhost:4000/health/liveliness" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch {}

if ($alreadyRunning) {
    Write-Host "($tag) LiteLLM proxy already running on port 4000 OK" -ForegroundColor Green
} else {
    $proxyBat = Join-Path $env:TEMP "dqg-start-proxy.bat"
    $proxyActivate = Join-Path $DQG_DIR ".venv\Scripts\Activate.ps1"
    Set-Content -Path $proxyBat -Value "@echo off`r`nchcp 65001 >nul 2>&1`r`nset PYTHONIOENCODING=utf-8`r`ncd /d `"$DQG_DIR`"`r`ncall `"$DQG_DIR\.venv\Scripts\activate.bat`"`r`nlitellm --config `"$litellmConfig`" --port 4000`r`npause"
    Start-Process -FilePath "cmd" -ArgumentList "/c", "`"$proxyBat`"" -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\dqg-proxy-stdout.log" -RedirectStandardError "$env:TEMP\dqg-proxy-stderr.log"
    Write-Host "($tag) LiteLLM proxy started (hidden)" -ForegroundColor Green
    Write-Host "($tag) Waiting for proxy to be ready..." -ForegroundColor Green
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try { $ready = (Invoke-WebRequest -Uri "http://localhost:4000/health/liveliness" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch {}
        if ($ready) { break }
        Write-Host "." -NoNewline
    }
    Write-Host ""
    if ($ready) {
        Write-Host "($tag) LiteLLM proxy is ready OK" -ForegroundColor Green
    } else {
        Write-Host "($tag) LiteLLM proxy not ready yet - check $env:TEMP\dqg-proxy-stderr.log" -ForegroundColor Yellow
    }
}

# -- Step 7: Start Web UI --

Write-Host ""
Write-Host "($tag) [7/7] Starting Web UI on port 8080..." -ForegroundColor Green
$WEB_LOG = Join-Path $env:TEMP "dqg-web.log"

$webAlreadyRunning = $false
try { $webAlreadyRunning = (Invoke-WebRequest -Uri "http://localhost:8080/api/status" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch {}

if ($webAlreadyRunning) {
    Write-Host "($tag) Web UI already running on port 8080 OK" -ForegroundColor Green
} else {
    $webBat = Join-Path $env:TEMP "dqg-start-web.bat"
    Set-Content -Path $webBat -Value "@echo off`r`nchcp 65001 >nul 2>&1`r`nset PYTHONIOENCODING=utf-8`r`ncd /d `"$DQG_DIR`"`r`ncall `"$DQG_DIR\.venv\Scripts\activate.bat`"`r`npython -m app.cli web --port 8080"
    Start-Process -FilePath "cmd" -ArgumentList "/c", "`"$webBat`"" -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\dqg-web-stdout.log" -RedirectStandardError "$env:TEMP\dqg-web-stderr.log"
    Write-Host "($tag) Web UI started (hidden)" -ForegroundColor Green
    Start-Sleep -Seconds 3
}

Write-Host ""
if ($modErrors -eq 0) {
    Write-Host "  =========================================================" -ForegroundColor Green
    Write-Host "        All checks passed - Services running" -ForegroundColor Green
    Write-Host "  =========================================================" -ForegroundColor Green
} else {
    Write-Host "  =========================================================" -ForegroundColor Yellow
    Write-Host "        Running with $modErrors issue-s-" -ForegroundColor Yellow
    Write-Host "  =========================================================" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Dashboard : http://localhost:8080/dashboard" -ForegroundColor Cyan
Write-Host "  Review    : http://localhost:8080" -ForegroundColor Cyan
Write-Host "  Proxy     : http://localhost:4000" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Web logs  : $env:TEMP\dqg-web-stderr.log" -ForegroundColor DarkGray
Write-Host "  Proxy logs: $env:TEMP\dqg-proxy-stderr.log" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  To stop   : scripts\win\stop.bat" -ForegroundColor Yellow
Write-Host ""

Start-Process "cmd" -ArgumentList "/c", "timeout /t 2 /nobreak >nul && start http://localhost:8080/dashboard"
