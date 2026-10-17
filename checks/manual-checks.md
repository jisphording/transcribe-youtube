# Antigravity manual checks

Run these checks as the macOS account that owns the backend LaunchAgent. The LaunchAgent configuration uses a restricted PATH, so use the absolute CLI path from `backend/.env`. Never copy credentials into the repository or send real vault content in a probe. Record results in `docs/antigravity-validation.md`; redact credentials, conversation IDs and private content. Current evidence and pending checks are summarized there.

### MC-01 — Subscription access under the backend user

**Claim**: The installed CLI can use the signed-in subscription without API-key routing.
**Doc**: docs/antigravity-validation.md
**Covers**: docs/antigravity-cli-contract.md
**Procedure**: As the backend user, run the configured absolute CLI with `--version` and `models`. Confirm Gemini 3.8 Flash (medium) is available. If sign-in is needed, complete it interactively with the CLI's supported flow; do not export or copy tokens. Run a harmless text-to-JSON probe using the fresh custom agent, key-stripped environment, disposable workspace and Seatbelt profile in `docs/antigravity-cli-contract.md`. Require exit 0, terminal `SUCCESS`, valid JSON about only the supplied harmless text and completion before the finite deadline. Record version, model and redacted result. A shell probe does not prove LaunchAgent access; complete MC-04 separately.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: none

### MC-02 — Full-process containment and adversarial canaries

**Claim**: Transcript-driven reads and commands remain denied under the production containment profile.
**Doc**: docs/antigravity-validation.md
**Covers**: docs/antigravity-cli-contract.md
**Procedure**: Follow the native denial and adversarial canary procedure in `docs/antigravity-cli-contract.md` using a new disposable workspace and vault fixture. Require native denial for repository/vault reads, hook-config reads, workspace writes and child command execution. Under the CLI profile, require terminal success, only the explicitly permitted session-local `manage_task` `Action=list` bookkeeping, and no write/shell/hook/MCP sentinel. Also require successful harmless JSON generation under the same profile. Confirm the resolved repository and configured vault paths are denied. Do not use real vault content or retry without containment.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: none

### MC-03 — Real note creation and Obsidian UI

**Claim**: A contained provider result is saved to the intended disposable vault and opens as a valid note in Obsidian.
**Doc**: docs/antigravity-validation.md
**Covers**: docs/antigravity-validation.md
**Procedure**: Choose or create a disposable vault, set its absolute path in `ANTIGRAVITY_VAULT_PATHS`, and confirm that path is denied to the CLI by the provider profile. Start the backend by the documented operator procedure. In Obsidian, import a harmless public captioned video using Antigravity with transcript output disabled. Confirm the backend reports subscription-quota usage, the note appears in the configured folder, frontmatter records the selected and resolved model, and Obsidian opens the note. Confirm no unexpected resource stub or transcript was created. Remove the disposable note and vault only after review.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: none

### MC-04 — LaunchAgent environment and service behavior

**Claim**: The actual backend LaunchAgent can discover the absolute CLI, use the signed-in account and reach the local backend health endpoint.
**Doc**: docs/antigravity-validation.md
**Covers**: docs/antigravity-validation.md
**Procedure**: Complete the README setup using an already installed and signed-in CLI; do not use the installer to install, update or sign in to Antigravity. Set `ANTIGRAVITY_CLI_PATH` to the absolute executable and any additional denied vault paths in `backend/.env`. Restart the backend with `backend/scripts/stop.sh` and `backend/scripts/start.sh`; do not alter the plist for the test. Confirm `backend/scripts/check.sh`, `launchctl list` and `curl -fsS http://127.0.0.1:8000/health` all report the expected service. Run one harmless contained subscription generation through the service. Confirm it exits by the configured deadline, reports the resolved model, and leaves no subprocess behind. Record launchd logs without copying credentials or transcript text.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: service not running during validation; operator run required

### MC-05 — Web/manual-paste and queued restart/retry

**Claim**: Other source paths and saved subscription settings continue to work through the service.
**Doc**: docs/antigravity-validation.md
**Covers**: docs/antigravity-validation.md
**Procedure**: Through the actual plugin/backend, import a public web article and a harmless manually pasted article, then queue a podcast RSS or Whisper-backed item with Antigravity. Restart the backend while a separate queued item is pending, confirm the saved model/options survive, and exercise the explicit retry path after a controlled fixture failure. Confirm the running-item cancel behavior and no paid-provider fallback or partial note on failure. Use only an operator-approved disposable destination and do not induce real subscription quota exhaustion.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: live service/UI check pending; fixture and automated evidence is documented separately
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: none
