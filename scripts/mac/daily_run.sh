#!/usr/bin/env bash
# ==============================================================================
# Job Deck macOS Runner - Daily Execution
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"

# Activate macOS virtual environment
if [ -d "${DIR}/.venv-mac" ]; then
    source "${DIR}/.venv-mac/bin/activate"
elif [ -d "${DIR}/.venv" ]; then
    source "${DIR}/.venv/bin/activate"
fi

echo "=== Job Deck Daily Run (macOS): Inbox -> New Jobs -> Scout -> Apply -> Fix -> Drafts -> Briefing ==="
python -u daily_run.py "$@" 2>&1 | tee -a daily_run.log

BRIEFING_DATE="$(date +"%Y-%m-%d")"
BRIEFING_FILE="${DIR}/briefings/${BRIEFING_DATE}.md"
if [ -f "$BRIEFING_FILE" ]; then
    echo ""
    echo "=== Daily Briefing Summary ==="
    cat "$BRIEFING_FILE"
fi
