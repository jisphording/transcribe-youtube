#!/usr/bin/env bash
# Install the YT-to-Obsidian backend as a native macOS launchd agent.
# Idempotent: re-run any time to reapply.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BACKEND_DIR"

LABEL="com.johannes.yt-obsidian"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/yt-obsidian"
PLIST_TEMPLATE="$BACKEND_DIR/${LABEL}.plist.template"
PLIST_INSTALLED="$LAUNCH_AGENTS_DIR/${LABEL}.plist"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
err()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }
step() { printf '\n\033[1;34m▸\033[0m \033[1m%s\033[0m\n' "$*"; }

# ─── 1. Prerequisites ────────────────────────────────────────────────────────

step "Checking prerequisites"

if ! command -v brew >/dev/null 2>&1; then
    err "Homebrew is not installed. See https://brew.sh"
    exit 1
fi
ok "Homebrew found: $(brew --prefix)"

for tool in uv deno; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        warn "$tool not installed — installing via brew"
        brew install "$tool"
    fi
    ok "$tool: $(command -v "$tool")"
done

# ─── 2. .env ─────────────────────────────────────────────────────────────────

step "Checking backend/.env"

if [[ ! -f "$BACKEND_DIR/.env" ]]; then
    if [[ -f "$BACKEND_DIR/.env.example" ]]; then
        cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
        warn "Created backend/.env from .env.example"
        warn "Edit backend/.env and set ANTHROPIC_API_KEY before starting."
    else
        err "backend/.env is missing and no .env.example was found."
        exit 1
    fi
else
    ok "backend/.env present"
fi

# ─── 3. Python environment via uv ────────────────────────────────────────────

step "Creating Python 3.12 environment with uv"

# uv sync installs the pinned Python + all deps into .venv, writes uv.lock
uv sync --project "$BACKEND_DIR"
ok ".venv built at $BACKEND_DIR/.venv"

VENV_UVICORN="$BACKEND_DIR/.venv/bin/uvicorn"
[[ -x "$VENV_UVICORN" ]] || { err "expected uvicorn at $VENV_UVICORN after uv sync — did the install fail?"; exit 1; }
ok "uvicorn entrypoint: $VENV_UVICORN"

VENV_PYTHON_LINK="$BACKEND_DIR/.venv/bin/python3.12"
[[ -e "$VENV_PYTHON_LINK" ]] || VENV_PYTHON_LINK="$BACKEND_DIR/.venv/bin/python"
VENV_PYTHON_RESOLVED="$("$VENV_PYTHON_LINK" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
ok "Python binary (for FDA): $VENV_PYTHON_RESOLVED"

# ─── 4. Log directory ────────────────────────────────────────────────────────

step "Preparing log directory"
mkdir -p "$LOG_DIR"
ok "Logs: $LOG_DIR"

# ─── 5. Render and install the launchd plist ─────────────────────────────────

step "Installing launchd agent"

mkdir -p "$LAUNCH_AGENTS_DIR"

# Unload any running instance before overwriting
if launchctl list | grep -q "$LABEL"; then
    launchctl unload "$PLIST_INSTALLED" 2>/dev/null || true
fi

# Substitute placeholders. Use | as sed delimiter because paths contain /.
sed -e "s|__VENV_UVICORN__|$VENV_UVICORN|g" \
    -e "s|__BACKEND_DIR__|$BACKEND_DIR|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$PLIST_TEMPLATE" > "$PLIST_INSTALLED"

ok "Plist installed: $PLIST_INSTALLED"

launchctl load "$PLIST_INSTALLED"
ok "Agent loaded — backend will auto-start on login"

# ─── 6. Wait for health ──────────────────────────────────────────────────────

step "Waiting for backend to come up"

HEALTHY=0
for i in {1..20}; do
    if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
        ok "Backend healthy: http://localhost:8000"
        HEALTHY=1
        break
    fi
    sleep 0.5
done

# ─── 7. Full Disk Access guidance ────────────────────────────────────────────

step "Full Disk Access (required if the project lives on an external volume or you use Safari cookies)"

ON_EXTERNAL=0
case "$BACKEND_DIR" in
    /Volumes/*) ON_EXTERNAL=1 ;;
esac

EXTERNAL_WARNING=""
if [[ $ON_EXTERNAL -eq 1 ]]; then
    EXTERNAL_WARNING="
⚠  This project lives on an external volume ($BACKEND_DIR).
   macOS blocks launchd agents from reading external drives until the
   Python binary has Full Disk Access. Until you grant it, the backend
   will fail to start. THIS IS NOT OPTIONAL FOR YOUR SETUP.
"
fi

cat <<EOF
${EXTERNAL_WARNING}
Grant Full Disk Access to this binary:

    $VENV_PYTHON_RESOLVED

Steps:
  1. System Settings → Privacy & Security → Full Disk Access
  2. Click +
  3. Press ⌘⇧G and paste the path above
  4. Open, then toggle it on
  5. Run: $BACKEND_DIR/scripts/stop.sh && $BACKEND_DIR/scripts/start.sh

This single grant covers both:
  • reading files on your external volume (so launchd can start uvicorn), and
  • reading Safari cookies (so yt-dlp can authenticate without cookies.txt).

EOF

# Auto-open the Privacy pane when we know FDA is still needed.
if [[ $HEALTHY -eq 0 ]]; then
    err "Backend did NOT come up within 10s."
    if [[ $ON_EXTERNAL -eq 1 ]]; then
        err "This is almost certainly the external-volume permission problem above."
    fi
    echo "Opening System Settings → Privacy & Security → Full Disk Access…"
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" || true
    echo
    echo "After granting FDA, run:"
    echo "    $BACKEND_DIR/scripts/stop.sh && $BACKEND_DIR/scripts/start.sh"
    exit 1
else
    printf "Open the Privacy pane now to grant FDA for Safari cookies? [y/N] "
    read -r answer < /dev/tty || answer=""
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" || true
    fi
fi

echo
bold "✓ Install complete."
echo "  start:  scripts/start.sh"
echo "  stop:   scripts/stop.sh"
echo "  logs:   scripts/logs.sh"
echo "  check:  scripts/check.sh"
echo "  update: scripts/update.sh"
