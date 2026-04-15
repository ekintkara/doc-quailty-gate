$ErrorActionPreference = "Stop"

$tag = "dqg-setup"

Write-Host ""
Write-Host "  =========================================================" -ForegroundColor Cyan
Write-Host "     Doc Quality Gate - Setup Wizard" -ForegroundColor Cyan
Write-Host "  =========================================================" -ForegroundColor Cyan
Write-Host ""

$DQG_DIR = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# -- Cleanup existing processes --

Write-Host "($tag) Cleaning up existing processes..." -ForegroundColor Green
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

# -- Step 1: Create venv + install deps --

Write-Host ""
Write-Host "($tag) Step 1/7: Creating virtual environment..." -ForegroundColor Green
Set-Location $DQG_DIR

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

# -- Step 2: Configure .env --

Write-Host ""
Write-Host "($tag) Step 2/7: Configuring environment..." -ForegroundColor Green

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
        $promptMsg = "  Enter your Z.AI API key -or press Enter to skip-"
        $apiKey = Read-Host $promptMsg
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
        $promptMsg2 = "  Enter a master key -or press Enter for auto-generated-"
        $masterKey = Read-Host $promptMsg2
        if (-not $masterKey) { $masterKey = [guid]::NewGuid().ToString() }
        $envContent = $envContent.TrimEnd() + "`nLITELLM_MASTER_KEY=$masterKey`n"
        Write-Host "($tag) LiteLLM master key saved OK" -ForegroundColor Green
    }

    Set-Content ".env" $envContent
} else {
    Write-Host "($tag) Z.AI API key already configured OK" -ForegroundColor Green
    Write-Host "($tag) LiteLLM master key already configured OK" -ForegroundColor Green
}

# -- Step 3: Install Promptfoo --

Write-Host ""
Write-Host "($tag) Step 3/7: Checking Promptfoo..." -ForegroundColor Green
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
Write-Host "($tag) Step 4/7: Setting up opencode integration..." -ForegroundColor Green
$commandsDir = Join-Path $env:USERPROFILE ".config\opencode\commands"
New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null

$slashCmd = Join-Path $DQG_DIR ".opencode\commands\dqg.md"
if (Test-Path $slashCmd) {
    Copy-Item $slashCmd (Join-Path $commandsDir "dqg.md") -Force
    Write-Host "($tag) Slash command installed: /dqg -global- OK" -ForegroundColor Green
}

Write-Host ""
Write-Host "($tag) AGENTS.md template: $DQG_DIR\AGENTS.md" -ForegroundColor Green
Write-Host "  Copy it to your projects:" -ForegroundColor Cyan
Write-Host "  Copy-Item `"$DQG_DIR\AGENTS.md`" `"C:\path\to\project\AGENTS.md`"" -ForegroundColor Cyan

# -- Step 5: Verify Python modules --

Write-Host ""
Write-Host "($tag) Step 5/7: Verifying Python modules..." -ForegroundColor Green
$modErrors = 0

$modOk = $false
try { python -c "from app.config import load_app_config; load_app_config()" 2>$null; $modOk = $true } catch {}
if ($modOk) { Write-Host "  [OK] Config" -ForegroundColor Green } else { Write-Host "  [FAIL] Config" -ForegroundColor Red; $modErrors++ }

$modOk = $false
try { python -c "from app.stages.codebase_context import scan_project" 2>$null; $modOk = $true } catch {}
if ($modOk) { Write-Host "  [OK] Codebase scanner" -ForegroundColor Green } else { Write-Host "  [FAIL] Codebase scanner" -ForegroundColor Red; $modErrors++ }

$modOk = $false
try { python -c "from app.stages.cross_reference import run_cross_reference" 2>$null; $modOk = $true } catch {}
if ($modOk) { Write-Host "  [OK] Cross-reference" -ForegroundColor Green } else { Write-Host "  [FAIL] Cross-reference" -ForegroundColor Red; $modErrors++ }

$modOk = $false
try { python -c "from app.integrations.litellm_client import LiteLLMClient" 2>$null; $modOk = $true } catch {}
if ($modOk) { Write-Host "  [OK] LiteLLM client" -ForegroundColor Green } else { Write-Host "  [FAIL] LiteLLM client" -ForegroundColor Red; $modErrors++ }

$modOk = $false
try { python -c "from app.orchestrator import Orchestrator" 2>$null; $modOk = $true } catch {}
if ($modOk) { Write-Host "  [OK] Orchestrator" -ForegroundColor Green } else { Write-Host "  [FAIL] Orchestrator" -ForegroundColor Red; $modErrors++ }

$litellmProxyOk = $false
try { python -c "import litellm.proxy; print('ok')" 2>$null; $litellmProxyOk = $true } catch {}
if ($litellmProxyOk) { Write-Host "  [OK] LiteLLM proxy module" -ForegroundColor Green } else { Write-Host "  [FAIL] LiteLLM proxy module -missing deps-" -ForegroundColor Red; $modErrors++ }

# -- Step 6: Start LiteLLM proxy --

Write-Host ""
Write-Host "($tag) Step 6/7: Starting LiteLLM proxy on port 4000..." -ForegroundColor Green
$litellmConfig = Join-Path $DQG_DIR "config\litellm\config.yaml"

$alreadyRunning = $false
try { $alreadyRunning = (Invoke-WebRequest -Uri "http://localhost:4000/health/liveliness" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch {}

if ($alreadyRunning) {
    Write-Host "($tag) LiteLLM proxy already running on port 4000 OK" -ForegroundColor Green
} else {
    Start-Process -FilePath "powershell" -ArgumentList "-NoExit", "-Command", "`$env:PYTHONIOENCODING='utf-8'; cd '$DQG_DIR'; & '.\.venv\Scripts\Activate.ps1'; litellm --config '$litellmConfig' --port 4000" -WindowStyle Minimized
    Write-Host "($tag) LiteLLM proxy started in background -minimized window-" -ForegroundColor Green
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
        Write-Host "($tag) LiteLLM proxy not ready yet -check the minimized window-" -ForegroundColor Yellow
    }
}

# -- Step 7: Final health check --

Write-Host ""
Write-Host "($tag) Step 7/7: Running final health check..." -ForegroundColor Green
$errors = 0

$proxyOk = $false
try { $proxyOk = (Invoke-WebRequest -Uri "http://localhost:4000/health/liveliness" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
if ($proxyOk) {
    Write-Host "  [OK] LiteLLM proxy: healthy" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] LiteLLM proxy: not responding" -ForegroundColor Red
    $errors++
}

$envContent2 = Get-Content ".env" -Raw
if ($null -eq $envContent2) { $envContent2 = "" }
if ($envContent2 -match "ZAI_API_KEY=\S+") {
    Write-Host "  [OK] Z.AI API key: configured" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] Z.AI API key: missing" -ForegroundColor Red
    $errors++
}

if ($envContent2 -match "LITELLM_MASTER_KEY=\S+") {
    Write-Host "  [OK] LiteLLM master key: configured" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] LiteLLM master key: missing" -ForegroundColor Red
    $errors++
}

$dqgCmd = Join-Path $env:USERPROFILE ".config\opencode\commands\dqg.md"
if (Test-Path $dqgCmd) {
    Write-Host "  [OK] Slash command: /dqg installed" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] Slash command: /dqg not found" -ForegroundColor Red
    $errors++
}

$promptfooFinalOk = $false
try {
    $pfOut = cmd /c "npx promptfoo --version 2>&1"
    if ($LASTEXITCODE -eq 0) { $promptfooFinalOk = $true }
} catch {}
if ($promptfooFinalOk) {
    Write-Host "  [OK] Promptfoo: available" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Promptfoo: not found -evaluations will be limited-" -ForegroundColor Yellow
}

# -- Summary --

Write-Host ""
if ($errors -eq 0) {
    Write-Host "  =========================================================" -ForegroundColor Green
    Write-Host "              All checks passed - Setup Complete!" -ForegroundColor Green
    Write-Host "  =========================================================" -ForegroundColor Green
} else {
    Write-Host "  =========================================================" -ForegroundColor Yellow
    Write-Host "              Setup finished with $errors issue-s-" -ForegroundColor Yellow
    Write-Host "  =========================================================" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  LiteLLM proxy running on port 4000." -ForegroundColor Cyan
Write-Host "  In opencode, run:" -ForegroundColor Cyan
Write-Host "    /dqg path\to\document.md" -ForegroundColor White
Write-Host ""
Read-Host "Press Enter to close"
