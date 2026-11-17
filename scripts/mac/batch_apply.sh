#!/usr/bin/env bash
# ==============================================================================
# Job Deck macOS Runner - Multi-Worker Parallel Batch Application Runner
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"

if [ -d "${DIR}/.venv-mac" ]; then
    source "${DIR}/.venv-mac/bin/activate"
elif [ -d "${DIR}/.venv" ]; then
    source "${DIR}/.venv/bin/activate"
fi

echo "=== Launching Parallel Runner (macOS) ==="
python -u batch_apply_parallel.py "$@"
