#!/usr/bin/env bash
# ==============================================================================
# Job Deck macOS Runner - Continuous Pipeline Target Runner
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"

if [ -d "${DIR}/.venv-mac" ]; then
    source "${DIR}/.venv-mac/bin/activate"
elif [ -d "${DIR}/.venv" ]; then
    source "${DIR}/.venv/bin/activate"
fi

mkdir -p logs
DATE_STR="$(date +"%Y-%m-%d")"
LOG_FILE="${DIR}/logs/runner_${DATE_STR}.log"

echo "=== Launching Job Deck Target Runner (macOS) -> Logging to $LOG_FILE ==="
KEEP_AWAKE=""
command -v caffeinate >/dev/null 2>&1 && KEEP_AWAKE="caffeinate -i"  # idle sleep would stop runs mid-form
$KEEP_AWAKE python -u run_until_target.py "$@" 2>&1 | tee -a "$LOG_FILE"
