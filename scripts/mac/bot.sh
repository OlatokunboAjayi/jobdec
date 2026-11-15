#!/usr/bin/env bash
# ==============================================================================
# Job Deck macOS Runner - Telegram & iMessage Control Bot & Autonomous Watchdog
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"

if [ -d "${DIR}/.venv-mac" ]; then
    source "${DIR}/.venv-mac/bin/activate"
elif [ -d "${DIR}/.venv" ]; then
    source "${DIR}/.venv/bin/activate"
fi

echo "=== Launching Job Deck Control Bot & Watchdog (macOS) ==="
python -u telegram_bot_service.py "$@"
