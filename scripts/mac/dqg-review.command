#!/bin/bash
cd "$(dirname "$0")/../.."
DQG_DIR="$(pwd)"

echo "========================================================="
echo "    Doc Quality Gate - Review Wizard"
echo "========================================================="
echo ""

echo "Recent .md files:"
echo ""
find . -name "*.md" -not -path "./.venv/*" -not -path "./node_modules/*" -not -path "./outputs/*" -not -path "./.git/*" -not -name "README.md" -not -name "AGENTS.md" 2>/dev/null | head -20
echo ""

echo -n "Enter document path (relative or absolute): "
read DOC_PATH

if [[ -z "$DOC_PATH" ]]; then
    echo "No path entered. Exiting."
    read
    exit 1
fi

if [[ ! -f "$DOC_PATH" ]]; then
    echo "File not found: $DOC_PATH"
    read
    exit 1
fi

echo ""
echo -n "Document type (feature_spec, implementation_plan, etc) [auto-detect]: "
read DOC_TYPE

echo -n "Project path for cross-reference [current dir]: "
read PROJECT_PATH
PROJECT_PATH="${PROJECT_PATH:-.}"

echo ""
source .venv/bin/activate

CMD="python -m app.cli review $DOC_PATH"
[[ -n "$DOC_TYPE" ]] && CMD="$CMD -t $DOC_TYPE"
CMD="$CMD --project $PROJECT_PATH"

echo "Running: $CMD"
echo ""
eval "$CMD"

echo ""
echo "Press Enter to close..."
read
