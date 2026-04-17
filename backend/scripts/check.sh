#!/usr/bin/env bash
# Diagnostic runner. Tells you whether the backend is alive, whether the
# Python binary can read Safari/Chrome cookies (TCC), and whether yt-dlp
# can pull a known-good public video.
set -uo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.johannes.yt-obsidian"
TEST_VIDEO="https://www.youtube.com/watch?v=dQw4w9WgXcQ"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
err()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

section() { printf '\n\033[1;34m▸\033[0m \033[1m%s\033[0m\n' "$*"; }

section "launchd agent"
if launchctl list | grep -q "$LABEL"; then
    ok "agent '$LABEL' is loaded"
else
    err "agent '$LABEL' NOT loaded — run scripts/install.sh or scripts/start.sh"
fi

section "backend health"
if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
    ok "GET /health returned 200"
else
    err "backend unreachable at http://localhost:8000"
fi

section "Python environment"
VENV_PY="$BACKEND_DIR/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
    RESOLVED="$(readlink -f "$VENV_PY" 2>/dev/null || "$VENV_PY" -c 'import sys,os; print(os.path.realpath(sys.executable))')"
    ok "venv python: $VENV_PY"
    ok "resolved to: $RESOLVED"
else
    err "no venv at $BACKEND_DIR/.venv — run scripts/install.sh"
fi

section "deno (required for yt-dlp PO-token)"
if command -v deno >/dev/null 2>&1; then
    ok "deno: $(deno --version | head -1)"
else
    err "deno not in PATH — brew install deno"
fi

section "plugin settings"
for vault in "$HOME"/Documents/*/.obsidian/plugins/youtube-to-obsidian/data.json \
             /Volumes/*/**/.obsidian/plugins/youtube-to-obsidian/data.json; do
    [[ -f "$vault" ]] || continue
    browser=$("$VENV_PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get('cookieBrowser',''))" "$vault" 2>/dev/null || echo "?")
    echo "    $vault"
    echo "      cookieBrowser = '${browser:-<none>}'"
done

section "cookie access"
if [[ -x "$VENV_PY" ]]; then
    "$VENV_PY" - <<'PY'
import os, sys, glob
from pathlib import Path

home = Path.home()
targets = {
    "safari":  [home / "Library/Cookies/Cookies.binarycookies"],
    "chrome":  [home / "Library/Application Support/Google/Chrome/Default/Cookies"],
    "firefox": [Path(p) for p in glob.glob(str(home / "Library/Application Support/Firefox/Profiles/*/cookies.sqlite"))],
    "edge":    [home / "Library/Application Support/Microsoft Edge/Default/Cookies"],
    "brave":   [home / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies"],
}

for name, paths in targets.items():
    status = "not installed"
    for p in paths:
        if not p.exists():
            continue
        try:
            with open(p, "rb") as f:
                f.read(1)
            status = f"readable ({p})"
        except PermissionError:
            status = f"BLOCKED by TCC — grant Full Disk Access to this python ({p})"
        except OSError as e:
            status = f"error: {e} ({p})"
        break
    icon = "  ✓" if status.startswith("readable") else ("  !" if "BLOCKED" in status else "  -")
    print(f"{icon} {name:8s} {status}")
PY
fi

section "yt-dlp end-to-end (test video: dQw4w9WgXcQ)"
if [[ -x "$VENV_PY" ]]; then
    BROWSER_ARG=()
    if [[ "${1:-}" == "--browser" ]] && [[ -n "${2:-}" ]]; then
        BROWSER_ARG=(--cookies-from-browser "$2")
        echo "    using --cookies-from-browser $2"
    elif [[ -f "$BACKEND_DIR/cookies.txt" ]]; then
        BROWSER_ARG=(--cookies "$BACKEND_DIR/cookies.txt")
        echo "    using $BACKEND_DIR/cookies.txt"
    else
        echo "    using no cookies"
    fi
    TITLE="$("$VENV_PY" -m yt_dlp "${BROWSER_ARG[@]}" --skip-download --no-warnings --print title --no-playlist "$TEST_VIDEO" 2>&1 | tail -5)"
    if echo "$TITLE" | grep -qi "rick astley"; then
        ok "yt-dlp returned: $(echo "$TITLE" | tail -1)"
    else
        err "yt-dlp failed:"
        echo "$TITLE" | sed 's/^/      /'
    fi
fi

echo
bold "done. Usage: scripts/check.sh [--browser safari|chrome|firefox|edge|brave]"
