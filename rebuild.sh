#!/usr/bin/env bash
# Full project rebuild: pull latest, rebuild backend + plugin, restart backend.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
PLUGIN_DIR="$REPO_DIR/obsidian-plugin"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1;34m▸\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }

# ─── 1. Pull latest ──────────────────────────────────────────────────────────

step "Pulling latest from git"
cd "$REPO_DIR"
if [[ -d .git ]]; then
    git pull --ff-only
    ok "Repo up to date"
else
    echo "Not a git repo — skipping pull"
fi

# ─── 2. Backend ──────────────────────────────────────────────────────────────

step "Rebuilding backend (uv sync)"
uv sync --project "$BACKEND_DIR"
ok "Backend dependencies synced"

step "Restarting backend"
"$BACKEND_DIR/scripts/stop.sh" || true
"$BACKEND_DIR/scripts/start.sh"

# ─── 3. Plugin ───────────────────────────────────────────────────────────────

step "Rebuilding Obsidian plugin"
cd "$PLUGIN_DIR"
npm install
npm run build
ok "Plugin built and deployed"

echo
bold "✓ Rebuild complete."
