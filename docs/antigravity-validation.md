# Antigravity service validation

Validation date: 2026-10-17. The tested CLI was `agy` 1.4.0 at `/Users/jisphording/.local/bin/agy`; the selected and resolved model was `gemini-3.8-flash-medium` through the signed-in subscription. No API key was used for these contained model calls. Subscription quota remaining was not measured.

## Results

| Check | Result | Evidence and limit |
|---|---|---|
| CLI discovery and version | PASS | The absolute executable returned version `1.4.0` on 2026-10-17. `agy models` and key-free generation were recorded in the existing CLI contract evidence. |
| Signed-in subscription generation | PASS | Same-user contained probes and backend imports completed with no API-key routing. This establishes generation access at probe time, not current quota or a guaranteed account entitlement. |
| Containment and process handling | PASS | Native denials and adversarial canaries are recorded in `docs/antigravity-cli-contract.md`; provider timeout, bounded-output and process cleanup cases have automated coverage. |
| Basic YouTube captions | PASS | A contained YouTube pipeline call fetched captions and returned a complete note event. No real Obsidian vault write or UI inspection was part of that check. |
| Podcast transcript | PASS, fixture-backed | A contained queued podcast import used an RSS transcript fixture and wrote a complete note into a disposable vault. Apple/RSS discovery and Whisper audio transcription under LaunchAgent were not exercised. |
| Extended, focus and resources | PASS, fixture-backed | The contained long YouTube and queued podcast imports ran with focus, extended, resources and notes-only options. Resource grounding and transcript omission were inspected. |
| Long transcript | PASS, fixture-backed | A 13,000-character fixture completed through the contained long-import path. It does not establish a provider context ceiling. |
| Batch persistence and retry | PASS, automated fixtures | Backend tests cover saved model/options across restart and retry, quota failure without paid fallback or partial note, and running-item cancel semantics. A live service restart/retry was not run. |
| Web article and manual paste | PENDING | No live web/manual-paste import was run through the signed-in CLI in this validation. |
| LaunchAgent startup and CLI access | PENDING | On validation, `com.johannes.yt-obsidian` was not listed by `launchctl list`, and `http://127.0.0.1:8000/health` refused the connection. No LaunchAgent was installed, started or changed. Shell CLI availability does not establish LaunchAgent access. |
| Real Obsidian note creation and UI | PENDING | No user vault was modified. Run MC-03 in `checks/manual-checks.md` with an operator-selected disposable vault. |

## Service setup record

The CLI executable is outside the LaunchAgent's configured `PATH`, so set an absolute `ANTIGRAVITY_CLI_PATH` in `backend/.env`. `ANTIGRAVITY_TIMEOUT_SECONDS` defaults to 600 seconds. `ANTIGRAVITY_VAULT_PATHS` accepts additional colon-separated absolute paths to deny; the repository and `/Volumes` are denied by default. Restart the backend after editing `.env`, then verify `/health` and run `backend/scripts/check.sh` as described in the README. The app never installs or signs in to the CLI.

Transcript text is sent to Google for processing. The CLI's sessions and raw diagnostics may persist in its user data directory; local process logs are not a statement about Google's retention. Quota exhaustion, published context/output ceilings, and quota remaining were not verified. The application's 12,000-character segment limit, 600-second process deadline and 8 MB per-stream output cap are conservative implementation limits.

## Reproduction

Use `checks/manual-checks.md` for operator-run subscription, containment, real note and LaunchAgent procedures. Record the actual user, date, CLI version, selected/resolved model, terminal result and any unavailable checks. Redact credentials, conversation IDs, transcript text and private vault content. Never mark a UI or LaunchAgent check passed from a shell-only CLI probe.

The pending manual claims are:

- The installed CLI can use the signed-in subscription without API-key routing.
- Transcript-driven reads and commands remain denied under the production containment profile.
- A contained provider result is saved to the intended disposable vault and opens as a valid note in Obsidian.
- The actual backend LaunchAgent can discover the absolute CLI, use the signed-in account and reach the local backend health endpoint.
- Other source paths and saved subscription settings continue to work through the service.

These describe checks to perform, not completed results.
