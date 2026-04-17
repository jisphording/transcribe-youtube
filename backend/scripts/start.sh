#!/usr/bin/env bash
set -euo pipefail
LABEL="com.johannes.yt-obsidian"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -f "$PLIST" ]]; then
    echo "Not installed. Run scripts/install.sh first." >&2
    exit 1
fi

if launchctl list | grep -q "$LABEL"; then
    echo "Already running."
else
    launchctl load "$PLIST"
    echo "Loaded."
fi

for i in {1..20}; do
    if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
        echo "✓ Backend healthy: http://localhost:8000"
        exit 0
    fi
    sleep 0.5
done
echo "Backend did not come up in time. Check scripts/logs.sh" >&2
exit 1
