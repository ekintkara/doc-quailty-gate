#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dqg]${NC} $*"; }
warn() { echo -e "${YELLOW}[dqg]${NC} $*"; }
die()  { echo -e "${RED}[dqg]${NC} $*" >&2; exit 1; }

DQG_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo -e "${CYAN}"
echo "  ========================================================="
echo "     Doc Quality Gate - Starting"
echo "  ========================================================="
echo -e "${NC}"

cd "$DQG_DIR"

# -- Cleanup existing LiteLLM processes --

log "Cleaning up existing LiteLLM processes..."
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

log "[1/7] Creating virtual environment..."

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

# -- Step 2: Configure .env --

echo ""
log "[2/7] Configuring environment..."

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
    log "Z.AI API key OK"
fi

MASTER_KEY=$(grep "^LITELLM_MASTER_KEY=" .env 2>/dev/null | cut -d= -f2 || true)
if [[ -z "$MASTER_KEY" ]]; then
    NEEDS_MASTER=true
else
    log "LiteLLM master key OK"
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

# -- Step 3: Install Promptfoo --

echo ""
log "[3/7] Checking Promptfoo..."
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

# -- Step 4: opencode integration --

echo ""
log "[4/7] Setting up opencode integration..."
OPENCODE_COMMANDS_DIR="$HOME/.config/opencode/commands"
mkdir -p "$OPENCODE_COMMANDS_DIR"

if [[ -f "$DQG_DIR/.opencode/commands/dqg.md" ]]; then
    cp "$DQG_DIR/.opencode/commands/dqg.md" "$OPENCODE_COMMANDS_DIR/dqg.md"
    log "Slash command /dqg OK"
fi

OPENCODE_DIR="$HOME/.config/opencode"
mkdir -p "$OPENCODE_DIR"
echo "$DQG_DIR" > "$OPENCODE_DIR/dqg_home"
log "DQG home path saved OK"

# -- Step 5: Verify Python modules --

echo ""
log "[5/7] Verifying Python modules..."
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

# -- Step 6: Start LiteLLM proxy --

echo ""
log "[6/7] Starting LiteLLM proxy on port 4000..."

if curl -s "http://localhost:4000/health/liveliness" >/dev/null 2>&1; then
    log "LiteLLM proxy already running OK"
else
    PROXY_LOG="${TMPDIR:-/tmp}/litellm_proxy.log"
    PYTHONIOENCODING=utf-8 litellm --config "$DQG_DIR/config/litellm/config.yaml" --port 4000 &>"$PROXY_LOG" &
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
        warn "LiteLLM proxy not ready yet -check $PROXY_LOG-"
    fi
fi

# -- Step 7: Start Web UI --

echo ""
log "[7/7] Starting Web UI on port 8080..."

WEB_LOG="${TMPDIR:-/tmp}/dqg-web.log"

if curl -s "http://localhost:8080/api/status" >/dev/null 2>&1; then
    log "Web UI already running OK"
else
    PYTHONIOENCODING=utf-8 python -m app.cli web --port 8080 &>"$WEB_LOG" &
    WEB_PID=$!
    log "Web UI started in background (PID: $WEB_PID)"
    sleep 2
fi

echo ""
if [[ $MOD_ERRORS -eq 0 ]]; then
    echo -e "${GREEN}"
    echo "  ========================================================="
    echo "        All checks passed - Services running"
    echo "  ========================================================="
    echo -e "${NC}"
else
    echo -e "${YELLOW}"
    echo "  ========================================================="
    echo "        Running with $MOD_ERRORS issue(s)"
    echo "  ========================================================="
    echo -e "${NC}"
fi

echo -e "  Dashboard : ${CYAN}http://localhost:8080/dashboard${NC}"
echo -e "  Review    : ${CYAN}http://localhost:8080${NC}"
echo -e "  Proxy     : ${CYAN}http://localhost:4000${NC}"
echo ""
echo -e "  Web log   : ${WEB_LOG}"
echo -e "  Proxy log : ${PROXY_LOG:-${TMPDIR:-/tmp}/litellm_proxy.log}"
echo ""
echo -e "  ${YELLOW}To stop: scripts/linux/stop.sh${NC}"
echo ""

(sleep 2 && open "http://localhost:8080/dashboard" 2>/dev/null || xdg-open "http://localhost:8080/dashboard" 2>/dev/null || python3 -m webbrowser "http://localhost:8080/dashboard" 2>/dev/null) &
