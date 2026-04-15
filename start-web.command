#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
echo "Starting Doc Quality Gate Web UI..."
echo "Open http://localhost:8080 in your browser."
echo "Press Ctrl+C to stop."
echo ""
python -m app.cli web --port 8080
