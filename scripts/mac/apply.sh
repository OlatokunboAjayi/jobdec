#!/usr/bin/env bash
# ==============================================================================
# Job Deck macOS Runner - Single Job Application
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"

if [ -d "${DIR}/.venv-mac" ]; then
    source "${DIR}/.venv-mac/bin/activate"
elif [ -d "${DIR}/.venv" ]; then
    source "${DIR}/.venv/bin/activate"
fi

if [ -z "$1" ]; then
    echo "Usage: ./scripts/mac/apply.sh <job-url> [resume-path]"
    exit 1
fi

python -u fill.py "$@"
