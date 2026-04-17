#!/usr/bin/env bash
set -euo pipefail
LABEL="com.johannes.yt-obsidian"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -f "$PLIST" ]]; then
    echo "Not installed." >&2
    exit 0
fi

if launchctl list | grep -q "$LABEL"; then
    launchctl unload "$PLIST"
    echo "Unloaded."
else
    echo "Not running."
fi
