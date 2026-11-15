#!/usr/bin/env bash
# ==============================================================================
# Job Deck - macOS Dedicated Environment Setup
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "========================================================================"
echo "          JOB DECK - macOS Dedicated Environment Setup"
echo "========================================================================"

# 1. Platform Verification
OS_NAME="$(uname -s)"
if [ "$OS_NAME" != "Darwin" ]; then
    echo "[!] Warning: This script is configured for macOS (Darwin). Current OS: $OS_NAME"
    echo "    Continuing anyway..."
fi

ARCH_NAME="$(uname -m)"
echo "• Architecture detected: $ARCH_NAME"
if [ "$ARCH_NAME" = "arm64" ]; then
    echo "  (Apple Silicon detected: PyTorch Metal Performance Shaders supported)"
fi

# 2. Python Version Check
PYTHON_BIN=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        VER="$($cmd -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        MAJOR="$($cmd -c 'import sys; print(sys.version_info.major)')"
        MINOR="$($cmd -c 'import sys; print(sys.version_info.minor)')"
        if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON_BIN="$cmd"
            echo "• Found compatible Python: $PYTHON_BIN (v$VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "[ERROR] Python 3.10 or higher is required."
    echo "Please install it via Homebrew: 'brew install python' or from https://www.python.org"
    exit 1
fi

# 3. Dedicated macOS Virtual Environment
VENV_DIR="${DIR}/.venv-mac"
if [ -d "$VENV_DIR" ]; then
    echo "• Found existing macOS virtual environment at .venv-mac"
else
    echo "• Creating dedicated macOS virtual environment (.venv-mac)..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# Activate virtual environment
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "• Activated virtual environment: $(which python)"

# 4. Install Dependencies
echo "• Upgrading pip, setuptools, and wheel..."
pip install --upgrade pip setuptools wheel --quiet

echo "• Installing dependencies from requirements-mac.txt..."
pip install -r requirements-mac.txt

# 5. Playwright Browser Installation
echo "• Installing Playwright Chromium browser binary for macOS..."
playwright install chromium

# 6. Make macOS Runner Scripts Executable
if [ -d "${DIR}/scripts/mac" ]; then
    echo "• Granting execution permissions to macOS runner scripts..."
    chmod +x "${DIR}/scripts/mac/"*.sh 2>/dev/null || true
fi

# 7. Verification of Native macOS iMessage Bridge
echo "• Testing native macOS AppleScript integration..."
if command -v osascript >/dev/null 2>&1; then
    echo "  [OK] AppleScript (osascript) is available for native iMessage alerts."
else
    echo "  [INFO] osascript not found; iMessage bridge will use fallback REST API mode."
fi

echo ""
echo "========================================================================"
echo "          macOS Environment Setup Complete!"
echo "========================================================================"
echo "To use this environment in your terminal:"
echo "    source .venv-mac/bin/activate"
echo ""
echo "To run Job Deck onboarding & wizard:"
echo "    python setup_wizard.py"
echo ""
echo "To start the Telegram Control Bot & Watchdog:"
echo "    ./scripts/mac/bot.sh"
echo ""
echo "To execute a daily application run:"
echo "    ./scripts/mac/daily_run.sh"
echo "========================================================================"
