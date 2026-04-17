#!/usr/bin/env bash
set -euo pipefail

DQG_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$DQG_DIR/.venv/bin/activate"
PROXY_URL="http://localhost:4000"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dqg]${NC} $*"; }
warn() { echo -e "${YELLOW}[dqg]${NC} $*"; }
die()  { echo -e "${RED}[dqg]${NC} $*" >&2; exit 1; }

if [[ $# -lt 1 ]]; then
    die "Usage: dqg-review.sh <document_path> [doc_type] [project_path]"
    echo ""
    echo "  document_path  Path to the implementation document (required)"
    echo "  doc_type       feature_spec|implementation_plan|architecture_change|... (optional, auto-detected)"
    echo "  project_path   Path to project for cross-reference (optional, default: .)"
    exit 1
fi

DOC_PATH="$1"
DOC_TYPE="${2:-}"
PROJECT_PATH="${3:-.}"

if [[ ! -f "$DOC_PATH" ]]; then
    die "Document not found: $DOC_PATH"
fi

source "$VENV"

if ! curl -s "http://localhost:4000/health/liveliness" >/dev/null 2>&1; then
    warn "LiteLLM proxy is not running. Starting it in background..."
    PROXY_LOG="${TMPDIR:-/tmp}/litellm_proxy.log"
    PYTHONIOENCODING=utf-8 litellm --config "$DQG_DIR/config/litellm/config.yaml" --port 4000 &>"$PROXY_LOG" &
    PROXY_PID=$!
    log "Waiting for proxy to start (PID: $PROXY_PID)..."
    for i in $(seq 1 30); do
        sleep 2
        if curl -s "http://localhost:4000/health/liveliness" >/dev/null 2>&1; then
            log "Proxy is ready."
            break
        fi
        if [[ $i -eq 30 ]]; then
            die "Proxy failed to start. Check /tmp/litellm_proxy.log"
        fi
    done
fi

CMD="python -m app.cli review $DOC_PATH"
[[ -n "$DOC_TYPE" ]] && CMD="$CMD -t $DOC_TYPE"
[[ -n "$PROJECT_PATH" ]] && CMD="$CMD --project $PROJECT_PATH"

log "Running review: $CMD"
echo ""

eval "cd '$DQG_DIR' && $CMD"

RUN_DIR=$(ls -td "$DQG_DIR/outputs/runs"/*/ 2>/dev/null | head -1)
if [[ -n "$RUN_DIR" && -f "${RUN_DIR}report.md" ]]; then
    echo ""
    log "Report: ${RUN_DIR}report.md"
    log "Revised document: ${RUN_DIR}revised.md"
    echo ""
    log "--- Report Preview ---"
    head -50 "${RUN_DIR}report.md"
fi
