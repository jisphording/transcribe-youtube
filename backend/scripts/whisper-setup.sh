#!/usr/bin/env bash
# Set up local whisper.cpp + whisper-server LaunchAgent for podcast transcription.
# Detects an existing source build (~/whisper.cpp) first; otherwise installs via Homebrew.
# Idempotent: safe to re-run.

set -euo pipefail

LABEL="com.whisper.server"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_INSTALLED="$LAUNCH_AGENTS_DIR/${LABEL}.plist"
MODELS_DIR="$HOME/models"
MODEL_NAME="ggml-large-v3-turbo.bin"
MODEL_PATH="$MODELS_DIR/$MODEL_NAME"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL_NAME"
WHISPER_PORT="${WHISPER_PORT:-2022}"
SOURCE_DIR="$HOME/whisper.cpp"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
err()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }
step() { printf '\n\033[1;34m▸\033[0m \033[1m%s\033[0m\n' "$*"; }

# ─── 1. Pick install path: source build vs Homebrew ──────────────────────────

step "Detecting whisper.cpp installation"

WHISPER_SERVER_BIN=""
METAL_RESOURCES=""

if [[ -x "$SOURCE_DIR/build/bin/whisper-server" ]]; then
    WHISPER_SERVER_BIN="$SOURCE_DIR/build/bin/whisper-server"
    METAL_RESOURCES="$SOURCE_DIR/ggml/src/ggml-metal"
    [[ -d "$METAL_RESOURCES" ]] || METAL_RESOURCES="$SOURCE_DIR"
    ok "Source build detected at $SOURCE_DIR"
elif command -v whisper-server >/dev/null 2>&1; then
    WHISPER_SERVER_BIN="$(command -v whisper-server)"
    if command -v brew >/dev/null 2>&1; then
        METAL_RESOURCES="$(brew --prefix whisper-cpp 2>/dev/null)/share/whisper-cpp"
    fi
    ok "Homebrew install detected at $WHISPER_SERVER_BIN"
else
    warn "No whisper.cpp install found — installing via Homebrew"
    if ! command -v brew >/dev/null 2>&1; then
        err "Homebrew is not installed. See https://brew.sh"
        exit 1
    fi
    brew install whisper-cpp ffmpeg
    WHISPER_SERVER_BIN="$(command -v whisper-server)"
    METAL_RESOURCES="$(brew --prefix whisper-cpp)/share/whisper-cpp"
    ok "Installed whisper-cpp via Homebrew"
fi

[[ -n "$WHISPER_SERVER_BIN" ]] || { err "whisper-server binary not found after install"; exit 1; }

# ffmpeg is needed for the --convert flag to accept MP3/M4A directly
if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg not found — installing via Homebrew"
    brew install ffmpeg
fi
FFMPEG_BIN="$(command -v ffmpeg)"
FFMPEG_DIR="$(dirname "$FFMPEG_BIN")"
ok "ffmpeg: $FFMPEG_BIN"

# ─── 2. Download the model ───────────────────────────────────────────────────

step "Checking for whisper model: $MODEL_NAME"

mkdir -p "$MODELS_DIR"
if [[ -f "$MODEL_PATH" ]]; then
    ok "Model already present at $MODEL_PATH ($(du -h "$MODEL_PATH" | cut -f1))"
else
    warn "Downloading $MODEL_NAME (~800 MB) — this can take a few minutes"
    curl -L --fail --progress-bar -o "$MODEL_PATH" "$MODEL_URL"
    ok "Model downloaded to $MODEL_PATH"
fi

# ─── 3. Install LaunchAgent for whisper-server ───────────────────────────────

step "Installing LaunchAgent for whisper-server (auto-start on login)"

mkdir -p "$LAUNCH_AGENTS_DIR"

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    launchctl unload "$PLIST_INSTALLED" 2>/dev/null || true
fi

cat > "$PLIST_INSTALLED" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${WHISPER_SERVER_BIN}</string>
        <string>--model</string>
        <string>${MODEL_PATH}</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>${WHISPER_PORT}</string>
        <string>--convert</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>GGML_METAL_PATH_RESOURCES</key>
        <string>${METAL_RESOURCES}</string>
        <key>PATH</key>
        <string>${FFMPEG_DIR}:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>/tmp</string>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/tmp/whisper-server.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/whisper-server.err</string>
</dict>
</plist>
EOF
ok "LaunchAgent installed: $PLIST_INSTALLED"

launchctl load "$PLIST_INSTALLED"
ok "whisper-server registered (lazy-start: backend will spawn it on demand)"

# ─── 4. Smoke-test by kickstarting once, then stopping it ────────────────────

step "Smoke-testing whisper-server on http://127.0.0.1:${WHISPER_PORT}"

launchctl kickstart "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true

verified=0
for _ in {1..30}; do
    if curl -fs --max-time 1 "http://127.0.0.1:${WHISPER_PORT}/" -o /dev/null 2>&1; then
        verified=1
        break
    fi
    sleep 1
done

# Whether it came up or not, leave the LaunchAgent stopped — backend manages it.
launchctl kill SIGTERM "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true

if [[ "$verified" == "1" ]]; then
    ok "whisper-server smoke test passed (now stopped — will start on demand)"
    echo
    bold "✓ Whisper setup complete."
    echo "  Lifecycle: the Obsidian plugin and backend control it via /whisper/{start,stop}."
    echo "  Manual:    launchctl kickstart gui/$(id -u)/${LABEL}    # start"
    echo "             launchctl kill SIGTERM gui/$(id -u)/${LABEL} # stop"
    echo "  Logs:      tail -f /tmp/whisper-server.log /tmp/whisper-server.err"
    exit 0
fi

err "whisper-server did not respond within 30s during smoke test. Check /tmp/whisper-server.err"
exit 1
