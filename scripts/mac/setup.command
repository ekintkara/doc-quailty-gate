#!/usr/bin/env bash
# Double-clickable launcher for macOS
DIR="$(cd "$(dirname "$0")" && pwd)"
osascript -e 'tell application "Terminal" to do script "bash \"'"$DIR/setup.sh"'\"; exit"'
