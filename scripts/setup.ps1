$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   Doc Quality Gate - Setup Wizard     ║" -ForegroundColor Cyan
Write-Host "  ╚═══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

$DQG_DIR = Split-Path -Parent $PSScriptRoot

# ── Prerequisites ──

Write-Host "[dqg-setup] Checking prerequisites..." -ForegroundColor Green

$pythonOk = $false
try { $pythonOk = (python --version 2>&1) -match "3\.(1[1-9]|[2-9]\d)" } catch {}
if (-not $pythonOk) {
    Write-Host "[dqg-setup] ERROR: Python 3.11+ is required." -ForegroundColor Red
    exit 1
}
Write-Host "[dqg-setup] Python: $(python --version 2>&1) OK" -ForegroundColor Green

$nodeOk = $false
try { $nodeOk = (node --version 2>&1) -match "v" } catch {}
if (-not $nodeOk) {
    Write-Host "[dqg-setup] ERROR: Node.js 18+ is required for Promptfoo." -ForegroundColor Red
    exit 1
}
Write-Host "[dqg-setup] Node.js: $(node --version 2>&1) OK" -ForegroundColor Green

# ── Step 1: Create venv + install deps ──

Write-Host ""
Write-Host "[dqg-setup] Step 1/5: Creating virtual environment..." -ForegroundColor Green
Set-Location $DQG_DIR

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& ".venv\Scripts\Activate.ps1"

pip install -e ".[dev]" 2>&1 | Select-Object -Last 3
Write-Host "[dqg-setup] Dependencies installed OK" -ForegroundColor Green

# ── Step 2: Configure .env ──

Write-Host ""
Write-Host "[dqg-setup] Step 2/5: Configuring environment..." -ForegroundColor Green

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[dqg-setup] .env created from .env.example" -ForegroundColor Yellow
}

$envContent = Get-Content ".env" -Raw
if ($envContent -notmatch "ZAI_API_KEY=\S+" -or $envContent -match "ZAI_API_KEY=your_zai") {
    Write-Host ""
    Write-Host "[dqg-setup] Z.AI API key not set." -ForegroundColor Yellow
    Write-Host "  Get your key from: https://z.ai" -ForegroundColor Cyan
    $apiKey = Read-Host "  Enter your Z.AI API key (or press Enter to skip)"
    if ($apiKey) {
        $envContent = $envContent -replace "ZAI_API_KEY=.*", "ZAI_API_KEY=$apiKey"
        Set-Content ".env" $envContent
        Write-Host "[dqg-setup] Z.AI API key saved OK" -ForegroundColor Green
    } else {
        Write-Host "[dqg-setup] Skipped. Edit .env manually later." -ForegroundColor Yellow
    }
} else {
    Write-Host "[dqg-setup] Z.AI API key already configured OK" -ForegroundColor Green
}

# ── Step 3: Promptfoo ──

Write-Host ""
Write-Host "[dqg-setup] Step 3/5: Checking Promptfoo..." -ForegroundColor Green
try {
    $pfVer = npx promptfoo --version 2>&1
    Write-Host "[dqg-setup] Promptfoo: $pfVer OK" -ForegroundColor Green
} catch {
    Write-Host "[dqg-setup] Promptfoo will be auto-installed on first run via npx." -ForegroundColor Yellow
}

# ── Step 4: opencode integration ──

Write-Host ""
Write-Host "[dqg-setup] Step 4/5: Setting up opencode integration..." -ForegroundColor Green
$commandsDir = Join-Path $env:USERPROFILE ".config\opencode\commands"
New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null

$slashCmd = Join-Path $DQG_DIR ".opencode\commands\dqg.md"
if (Test-Path $slashCmd) {
    Copy-Item $slashCmd (Join-Path $commandsDir "dqg.md") -Force
    Write-Host "[dqg-setup] Slash command installed: /dqg (global) OK" -ForegroundColor Green
}

Write-Host ""
Write-Host "[dqg-setup] AGENTS.md template: $DQG_DIR\AGENTS.md" -ForegroundColor Green
Write-Host "  Copy it to your projects:" -ForegroundColor Cyan
Write-Host "  Copy-Item `"$DQG_DIR\AGENTS.md`" `"C:\path\to\project\AGENTS.md`"" -ForegroundColor Cyan

# ── Step 5: Verify ──

Write-Host ""
Write-Host "[dqg-setup] Step 5/5: Verification..." -ForegroundColor Green
& ".venv\Scripts\Activate.ps1"
python -c "from app.config import load_app_config; load_app_config(); print('  Config loading: OK')"
python -c "from app.stages.codebase_context import scan_project; print('  Codebase scanner: OK')"
python -c "from app.stages.cross_reference import run_cross_reference; print('  Cross-reference: OK')"

# ── Summary ──

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║              Setup Complete!                             ║" -ForegroundColor Green
Write-Host "  ╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick start:"
Write-Host ""
Write-Host "    1. Start LiteLLM proxy:"
Write-Host "       cd $DQG_DIR ; .\.venv\Scripts\Activate.ps1"
Write-Host "       litellm --config config\litellm\config.yaml --port 4000"
Write-Host ""
Write-Host "    2. Review a document (CLI):"
Write-Host "       python -m app.cli review path\to\doc.md --project ."
Write-Host ""
Write-Host "    3. Or use the wrapper:"
Write-Host "       powershell -File $DQG_DIR\scripts\dqg-review.ps1 path\to\doc.md"
Write-Host ""
Write-Host "    4. In opencode:"
Write-Host "       /dqg path\to\document.md"
Write-Host ""
Write-Host "    5. Web UI:"
Write-Host "       python -m app.cli web --port 8080"
Write-Host ""
