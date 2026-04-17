#!/usr/bin/env bash
# Pull latest code, re-sync dependencies from the lockfile, restart.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(cd "$BACKEND_DIR/.." && pwd)"

cd "$REPO_DIR"
if [[ -d .git ]]; then
    echo "▸ git pull"
    git pull --ff-only
fi

cd "$BACKEND_DIR"
echo "▸ uv sync"
uv sync --project "$BACKEND_DIR"

echo "▸ restarting backend"
"$BACKEND_DIR/scripts/stop.sh" || true
"$BACKEND_DIR/scripts/start.sh"
