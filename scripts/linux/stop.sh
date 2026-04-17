#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dqg]${NC} $*"; }
warn() { echo -e "${YELLOW}[dqg]${NC} $*"; }

echo -e "${CYAN}"
echo "  ========================================================="
echo "     Doc Quality Gate - Stopping"
echo "  ========================================================="
echo -e "${NC}"

# -- Stop Web UI --

log "Stopping Web UI (port 8080)..."
WEB_PIDS=$(pgrep -f "app.cli web" 2>/dev/null || true)
if [[ -n "$WEB_PIDS" ]]; then
    echo "$WEB_PIDS" | xargs kill 2>/dev/null || true
    log "Web UI stopped"
else
    warn "Web UI not running"
fi

# -- Stop LiteLLM proxy --

log "Stopping LiteLLM proxy (port 4000)..."
LITELLM_PIDS=$(pgrep -f "litellm.*--port 4000" 2>/dev/null || true)
if [[ -n "$LITELLM_PIDS" ]]; then
    echo "$LITELLM_PIDS" | xargs kill 2>/dev/null || true
    log "LiteLLM proxy stopped"
else
    warn "LiteLLM proxy not running"
fi

echo ""
log "All services stopped."
echo ""
