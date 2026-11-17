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

## Real-import verification (plan 04, phase 3)

Deferred on 2026-11-17: the machine used for the plan run has no signed-in Antigravity CLI, real provider credentials or Obsidian vault, so these checks were never run. Run them on a machine that has all three, then fill in `Last verified`, `Verified against` (commit), `Status` and a redacted result (date, provider, outcome; no transcript text, credentials or private vault content). A defect found here becomes a new debug run or a `plan/improvements.md` entry, not a code change in the same step.

### MC-06 — Antigravity failures are classified (no bare exit 71)

**Claim**: A failed Antigravity import in the Queue view shows a classified message (sandbox, quota, auth, network, model) or "exit N … unclassified", never a bare "Antigravity CLI failed (exit 71)".
**Doc**: docs/antigravity-cli-contract.md
**Covers**: backend/antigravity.py
**Procedure**: Restart the backend (`backend/scripts/stop.sh`, then `backend/scripts/start.sh`) after checking the Queue view has no running item, and confirm `curl -fsS http://127.0.0.1:8000/health`. In Obsidian, queue a YouTube video longer than 1 hour with provider "Antigravity". Run it directly on the machine, not inside another agent's sandbox (a nested sandbox reproduces `sandbox_apply: Operation not permitted`, exit 71, and is not a valid test). If the import succeeds, record that. If it fails, record the exact Queue view message and confirm it is classified.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: operator run required on a machine with signed-in Antigravity CLI, backend LaunchAgent and Obsidian

### MC-07 — Extended summary with resources on a real cloud provider

**Claim**: A real extended import produces an extended section of roughly 40-60% of the transcript's word count (advisory, not enforced), resources that all occur in the transcript, and resources placed last in the note.
**Doc**: CLAUDE.md
**Covers**: backend/main.py
**Procedure**: Queue a 30-60 minute YouTube video that names tools or products, with extended summary and resources on, on a real cloud provider (Claude API, Claude subscription or OpenRouter). Open the finished note. Compare the extended section's word count with the transcript's, check that every listed resource appears in the transcript, and confirm the resources section is the last section. Record provider, model, video length, extended/transcript word counts and outcome.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: operator run required on a machine with a real cloud provider and Obsidian

### MC-08 — Resource table and Bases block in Obsidian

**Claim**: With core Bases enabled, the note's Markdown resource table is alphabetical and the collapsible "Sort and filter resources" Bases block lists the created stubs and sorts and filters.
**Doc**: CLAUDE.md
**Covers**: backend/note.py
**Procedure**: Using the note from MC-07, with the core Bases plugin enabled, open it in Obsidian. Confirm the Markdown resource table is in alphabetical order, expand "Sort and filter resources", confirm it lists the stubs created in the resources folder, and confirm sorting and filtering work.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: depends on MC-07; operator run required in Obsidian

## Real imports on the Claude subscription

Deferred on 2026-11-16: these imports need Obsidian and live subscription quota. Already verified: one YouTube single import produced a note with `import_model: "claude-cli:sonnet"`. For each check, set the vault's provider to "Claude subscription" (`claude-cli`), confirm `curl -fsS http://127.0.0.1:8000/health`, then check that the note's `import_model` / `import_resolved_model` start with `claude-cli:` and that the note is in the expected folder below `MEDIA_Transcripts`. Record the date, model and outcome only, with no transcript text or credentials.

### MC-09 — Apple Podcasts episode on the Claude subscription

**Claim**: A podcast episode imports end to end on the subscription, including Whisper when the feed has no RSS transcript.
**Doc**: docs/claude-cli-contract.md
**Covers**: backend/claude_cli.py
**Procedure**: In the Import modal, paste a short `podcasts.apple.com/...?i=...` episode URL and import it. Confirm the note lands in `Podcasts` with `claude-cli:` model frontmatter.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: operator run required in Obsidian with subscription quota

### MC-10 — Web article on the Claude subscription

**Claim**: A web article imports through `/process` (in the modal, not the queue) on the subscription.
**Doc**: docs/claude-cli-contract.md
**Covers**: backend/claude_cli.py
**Procedure**: In the Import modal, paste a blog or news URL and import it. Confirm the note lands in `Articles` with `claude-cli:` model frontmatter.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: operator run required in Obsidian with subscription quota

### MC-11 — 3-item channel batch on the Claude subscription

**Claim**: A 3-item YouTube channel batch finishes on the subscription without errors, and a usage limit (if hit) pauses the lane until the reset time while the item stays pending.
**Doc**: docs/claude-cli-contract.md
**Covers**: backend/main.py
**Procedure**: Paste a YouTube channel URL, set the count to 3, preview, then confirm. Items are paced 15–45 s apart. In the Queue view, all three items must end under Finished without errors. If a real usage-limit event occurs, check the pause message and that the lane resumes after `resets_at` (see "Error mapping" in `docs/claude-cli-contract.md`).
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: operator run required in Obsidian with subscription quota
