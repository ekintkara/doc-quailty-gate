#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dqg-setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[dqg-setup]${NC} $*"; }
die()  { echo -e "${RED}[dqg-setup]${NC} $*" >&2; exit 1; }

DQG_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo -e "${CYAN}"
echo "  ╔═══════════════════════════════════════╗"
echo "  ║   Doc Quality Gate - Setup Wizard     ║"
echo "  ╚═══════════════════════════════════════╝"
echo -e "${NC}"

# ── Prerequisites ──

log "Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    die "Python 3.11+ is required. Install it first."
fi
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
log "Python: $PYTHON_VERSION ✓"

if ! command -v node &>/dev/null && ! command -v npx &>/dev/null; then
    die "Node.js 18+ is required for Promptfoo. Install it first."
fi
log "Node.js: $(node --version 2>/dev/null || echo 'via npx') ✓"

if ! command -v uv &>/dev/null; then
    warn "uv not found. Installing..."
    curl -fsSL https://astral.sh/uv/install.sh | sh 2>/dev/null || die "Failed to install uv. Install manually: https://docs.astral.sh/uv/"
    export PATH="$HOME/.local/bin:$PATH"
fi
log "uv: $(uv --version) ✓"

echo ""

# ── Step 1: Create venv + install deps ──

log "Step 1/5: Creating virtual environment and installing dependencies..."
cd "$DQG_DIR"
uv venv .venv --python 3.12 2>/dev/null || python3 -m venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]" 2>&1 | tail -3
log "Dependencies installed ✓"
echo ""

# ── Step 2: Configure .env ──

log "Step 2/5: Configuring environment..."

if [[ ! -f .env ]]; then
    cp .env.example .env
    warn ".env created from .env.example"
fi

ZAI_KEY=$(grep "^ZAI_API_KEY=" .env 2>/dev/null | cut -d= -f2)
if [[ -z "$ZAI_KEY" || "$ZAI_KEY" == "your_zai_api_key_here" ]]; then
    echo ""
    warn "Z.AI API key not set."
    echo -e "  Get your key from: ${CYAN}https://z.ai${NC}"
    echo -n "  Enter your Z.AI API key (or press Enter to skip): "
    read -r API_KEY_INPUT
    if [[ -n "$API_KEY_INPUT" ]]; then
        if grep -q "^ZAI_API_KEY=" .env; then
            sed -i "s|^ZAI_API_KEY=.*|ZAI_API_KEY=$API_KEY_INPUT|" .env
        else
            echo "ZAI_API_KEY=$API_KEY_INPUT" >> .env
        fi
        log "Z.AI API key saved ✓"
    else
        warn "Skipped. Edit .env manually later."
    fi
else
    log "Z.AI API key already configured ✓"
fi
echo ""

# ── Step 3: Install Promptfoo ──

log "Step 3/5: Checking Promptfoo..."
if ! npx promptfoo --version &>/dev/null; then
    warn "Promptfoo not found. It will be auto-installed on first run via npx."
else
    log "Promptfoo: $(npx promptfoo --version 2>/dev/null | head -1) ✓"
fi
echo ""

# ── Step 4: opencode integration ──

log "Step 4/5: Setting up opencode integration..."
OPENCODE_COMMANDS_DIR="$HOME/.config/opencode/commands"
mkdir -p "$OPENCODE_COMMANDS_DIR"

if [[ -f "$DQG_DIR/.opencode/commands/dqg.md" ]]; then
    cp "$DQG_DIR/.opencode/commands/dqg.md" "$OPENCODE_COMMANDS_DIR/dqg.md"
    log "Slash command installed: /dqg (global) ✓"
else
    warn "Slash command file not found at .opencode/commands/dqg.md"
fi

echo ""
log "AGENTS.md template is at: $DQG_DIR/AGENTS.md"
log "Copy it to your target projects:"
echo -e "  ${CYAN}cp $DQG_DIR/AGENTS.md /path/to/your/project/AGENTS.md${NC}"
echo ""

# ── Step 5: Verify ──

log "Step 5/5: Verification..."
source .venv/bin/activate
python -c "from app.config import load_app_config; load_app_config(); print('  Config loading: OK')" || die "Config loading failed"
python -c "from app.stages.codebase_context import scan_project; print('  Codebase scanner: OK')" || die "Module import failed"
python -c "from app.stages.cross_reference import run_cross_reference; print('  Cross-reference: OK')" || die "Module import failed"
echo ""

# ── Summary ──

echo -e "${GREEN}"
echo "  ╔═══════════════════════════════════════════════════════════╗"
echo "  ║              Setup Complete!                             ║"
echo "  ╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo "  Quick start:"
echo ""
echo "    1. Start LiteLLM proxy:"
echo "       cd $DQG_DIR && source .venv/bin/activate"
echo "       litellm --config config/litellm/config.yaml --port 4000"
echo ""
echo "    2. Review a document (CLI):"
echo "       python -m app.cli review path/to/doc.md --project /path/to/project"
echo ""
echo "    3. Or use the wrapper (auto-starts proxy):"
echo "       bash $DQG_DIR/scripts/dqg-review.sh path/to/doc.md"
echo ""
echo "    4. In opencode:"
echo "       /dqg path/to/document.md"
echo ""
echo "    5. Web UI:"
echo "       python -m app.cli web --port 8080"
echo ""
echo -e "  Docs: ${CYAN}$DQG_DIR/docs/${NC}"
echo ""
