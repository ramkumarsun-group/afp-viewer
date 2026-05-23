#!/usr/bin/env bash
# Quick launcher for AFP Viewer on macOS
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv on first run
if [ ! -d ".venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt -q
    echo "Done."
fi

exec .venv/bin/python main.py "$@"
