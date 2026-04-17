#!/usr/bin/env bash
# Tail both stdout and stderr. Pass -n N to show last N lines before following.
set -euo pipefail
LOG_DIR="$HOME/Library/Logs/yt-obsidian"

if [[ ! -d "$LOG_DIR" ]]; then
    echo "No log directory at $LOG_DIR (backend not installed yet?)." >&2
    exit 1
fi

touch "$LOG_DIR/stdout.log" "$LOG_DIR/stderr.log"
exec tail -F "$@" "$LOG_DIR/stdout.log" "$LOG_DIR/stderr.log"
