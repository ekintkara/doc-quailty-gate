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

DQG_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo -e "${CYAN}"
echo "  ========================================================="
echo "     Doc Quality Gate - Setup Wizard"
echo "  ========================================================="
echo -e "${NC}"

# -- Cleanup existing processes --

log "Cleaning up existing processes..."
LITELLM_PIDS=$(pgrep -f "litellm.*--port 4000" 2>/dev/null || true)
if [[ -n "$LITELLM_PIDS" ]]; then
    echo "$LITELLM_PIDS" | xargs kill 2>/dev/null || true
    warn "Stopped existing LiteLLM processes"
    sleep 2
else
    log "No existing LiteLLM processes found"
fi

# -- Prerequisites --

log "Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    die "Python 3.11+ is required. Install it first."
fi
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [[ "$PYTHON_MINOR" -lt 11 ]]; then
    die "Python 3.11+ is required. Found: $PYTHON_VERSION"
fi
log "Python: $PYTHON_VERSION OK"

if ! command -v node &>/dev/null && ! command -v npx &>/dev/null; then
    die "Node.js 18+ is required. Install it first."
fi
log "Node.js: $(node --version 2>/dev/null || echo 'via npx') OK"

echo ""

# -- Step 1: Create venv + install deps --

log "Step 1/7: Creating virtual environment..."
cd "$DQG_DIR"

if [[ ! -d .venv ]]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

log "Installing Python dependencies..."
pip install -e ".[dev]" 2>&1 | tail -1

log "Installing LiteLLM proxy dependencies..."
pip install "litellm[proxy]" 2>&1 | tail -1

if ! python -c "import orjson" 2>/dev/null; then
    log "Installing orjson..."
    pip install orjson --no-build-isolation 2>&1 | tail -1
fi

log "Dependencies installed OK"
echo ""

# -- Step 2: Configure .env --

log "Step 2/7: Configuring environment..."

if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        warn ".env created from .env.example"
    else
        touch .env
        warn ".env created (empty)"
    fi
fi

ZAI_KEY=$(grep "^ZAI_API_KEY=" .env 2>/dev/null | cut -d= -f2 || true)
NEEDS_KEY=false
NEEDS_MASTER=false

if [[ -z "$ZAI_KEY" || "$ZAI_KEY" == "your_zai_api_key_here" ]]; then
    NEEDS_KEY=true
else
    log "Z.AI API key already configured OK"
fi

MASTER_KEY=$(grep "^LITELLM_MASTER_KEY=" .env 2>/dev/null | cut -d= -f2 || true)
if [[ -z "$MASTER_KEY" ]]; then
    NEEDS_MASTER=true
else
    log "LiteLLM master key already configured OK"
fi

if [[ "$NEEDS_KEY" == "true" ]]; then
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
        log "Z.AI API key saved OK"
    else
        warn "Skipped. Edit .env manually later."
    fi
fi

if [[ "$NEEDS_MASTER" == "true" ]]; then
    echo ""
    warn "LiteLLM master key not set."
    echo "  This is required for the proxy to start."
    echo -n "  Enter a master key (or press Enter for auto-generated): "
    read -r MASTER_INPUT
    if [[ -z "$MASTER_INPUT" ]]; then
        MASTER_INPUT=$(python3 -c "import uuid; print(uuid.uuid4())")
    fi
    echo "LITELLM_MASTER_KEY=$MASTER_INPUT" >> .env
    log "LiteLLM master key saved OK"
fi

echo ""

# -- Step 3: Install Promptfoo --

log "Step 3/7: Checking Promptfoo..."
PROMPTFOO_OK=false
if timeout 15 npx promptfoo --version &>/dev/null; then
    PROMPTFOO_OK=true
    log "Promptfoo: $(npx promptfoo --version 2>/dev/null | head -1) OK"
fi

if [[ "$PROMPTFOO_OK" != "true" ]]; then
    warn "Installing Promptfoo globally..."
    npm install -g promptfoo 2>&1 | tail -1
    sleep 2
    if npx promptfoo --version &>/dev/null; then
        log "Promptfoo: $(npx promptfoo --version 2>/dev/null | head -1) OK"
    else
        warn "Promptfoo install skipped - will use npx on demand"
    fi
fi
echo ""

# -- Step 4: opencode integration --

log "Step 4/7: Setting up opencode integration..."
OPENCODE_COMMANDS_DIR="$HOME/.config/opencode/commands"
mkdir -p "$OPENCODE_COMMANDS_DIR"

if [[ -f "$DQG_DIR/.opencode/commands/dqg.md" ]]; then
    cp "$DQG_DIR/.opencode/commands/dqg.md" "$OPENCODE_COMMANDS_DIR/dqg.md"
    log "Slash command installed: /dqg (global) OK"
fi

echo ""
log "AGENTS.md template: $DQG_DIR/AGENTS.md"
echo -e "  Copy it to your projects:"
echo -e "  ${CYAN}cp $DQG_DIR/AGENTS.md /path/to/your/project/AGENTS.md${NC}"
echo ""

# -- Step 5: Verify Python modules --

log "Step 5/7: Verifying Python modules..."
source .venv/bin/activate
MOD_ERRORS=0

if python -c "from app.config import load_app_config; load_app_config()" 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} Config"
else
    echo -e "  ${RED}[FAIL]${NC} Config"; MOD_ERRORS=$((MOD_ERRORS + 1))
fi

if python -c "from app.stages.codebase_context import scan_project" 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} Codebase scanner"
else
    echo -e "  ${RED}[FAIL]${NC} Codebase scanner"; MOD_ERRORS=$((MOD_ERRORS + 1))
fi

if python -c "from app.stages.cross_reference import run_cross_reference" 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} Cross-reference"
else
    echo -e "  ${RED}[FAIL]${NC} Cross-reference"; MOD_ERRORS=$((MOD_ERRORS + 1))
fi

if python -c "from app.integrations.litellm_client import LiteLLMClient" 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} LiteLLM client"
else
    echo -e "  ${RED}[FAIL]${NC} LiteLLM client"; MOD_ERRORS=$((MOD_ERRORS + 1))
fi

if python -c "from app.orchestrator import Orchestrator" 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} Orchestrator"
else
    echo -e "  ${RED}[FAIL]${NC} Orchestrator"; MOD_ERRORS=$((MOD_ERRORS + 1))
fi

if python -c "import litellm.proxy" 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} LiteLLM proxy module"
else
    echo -e "  ${RED}[FAIL]${NC} LiteLLM proxy module -missing deps-"; MOD_ERRORS=$((MOD_ERRORS + 1))
fi

echo ""

# -- Step 6: Start LiteLLM proxy --

log "Step 6/7: Starting LiteLLM proxy on port 4000..."
ALREADY_RUNNING=false
if curl -s "http://localhost:4000/health/liveliness" >/dev/null 2>&1; then
    ALREADY_RUNNING=true
    log "LiteLLM proxy already running on port 4000 OK"
else
    PYTHONIOENCODING=utf-8 litellm --config "$DQG_DIR/config/litellm/config.yaml" --port 4000 &>/tmp/litellm_proxy.log &
    PROXY_PID=$!
    log "LiteLLM proxy started in background (PID: $PROXY_PID)"
    log "Waiting for proxy to be ready..."
    READY=false
    for i in $(seq 1 30); do
        sleep 2
        if curl -s "http://localhost:4000/health/liveliness" >/dev/null 2>&1; then
            READY=true
            break
        fi
        echo -n "."
    done
    echo ""
    if [[ "$READY" == "true" ]]; then
        log "LiteLLM proxy is ready OK"
    else
        warn "LiteLLM proxy not ready yet -check /tmp/litellm_proxy.log-"
    fi
fi

# -- Step 7: Final health check --

echo ""
log "Step 7/7: Running final health check..."
ERRORS=0

if curl -s "http://localhost:4000/health/liveliness" >/dev/null 2>&1; then
    echo -e "  ${GREEN}[OK]${NC} LiteLLM proxy: healthy"
else
    echo -e "  ${RED}[FAIL]${NC} LiteLLM proxy: not responding"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "^ZAI_API_KEY=.\+" .env 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} Z.AI API key: configured"
else
    echo -e "  ${RED}[FAIL]${NC} Z.AI API key: missing"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "^LITELLM_MASTER_KEY=.\+" .env 2>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} LiteLLM master key: configured"
else
    echo -e "  ${RED}[FAIL]${NC} LiteLLM master key: missing"
    ERRORS=$((ERRORS + 1))
fi

if [[ -f "$HOME/.config/opencode/commands/dqg.md" ]]; then
    echo -e "  ${GREEN}[OK]${NC} Slash command: /dqg installed"
else
    echo -e "  ${RED}[FAIL]${NC} Slash command: /dqg not found"
    ERRORS=$((ERRORS + 1))
fi

if npx promptfoo --version &>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} Promptfoo: available"
else
    echo -e "  ${YELLOW}[WARN]${NC} Promptfoo: not found -evaluations will be limited-"
fi

# -- Summary --

echo ""
if [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}"
    echo "  ========================================================="
    echo "              All checks passed - Setup Complete!"
    echo "  ========================================================="
    echo -e "${NC}"
else
    echo -e "${YELLOW}"
    echo "  ========================================================="
    echo "              Setup finished with $ERRORS issue(s)"
    echo "  ========================================================="
    echo -e "${NC}"
fi
echo ""
echo -e "  LiteLLM proxy running on port 4000."
echo -e "  In opencode, run:"
echo -e "    ${CYAN}/dqg path/to/document.md${NC}"
echo ""
