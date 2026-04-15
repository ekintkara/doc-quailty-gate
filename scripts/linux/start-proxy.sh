#!/bin/bash
cd "$(dirname "$0")/../.."
DQG_DIR="$(pwd)"
source .venv/bin/activate
echo "Starting LiteLLM Proxy on port 4000..."
echo "Press Ctrl+C to stop."
echo ""
litellm --config config/litellm/config.yaml --port 4000
