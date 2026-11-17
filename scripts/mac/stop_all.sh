#!/usr/bin/env bash
# ==============================================================================
# Job Deck macOS Runner - Stop All Running Job Deck Processes
# ==============================================================================

echo "Scanning for active Job Deck processes..."

# Stop batch runners, fillers, and bots
pkill -f "telegram_bot_service.py" 2>/dev/null && echo "• Stopped Telegram Bot Service." || true
pkill -f "batch_apply_parallel.py" 2>/dev/null && echo "• Stopped Parallel Batch Runner." || true
pkill -f "run_until_target.py" 2>/dev/null && echo "• Stopped Target Runner." || true
pkill -f "daily_run.py" 2>/dev/null && echo "• Stopped Daily Run." || true
pkill -f "fill.py" 2>/dev/null && echo "• Stopped active fill workers." || true

echo "All Job Deck processes terminated."
