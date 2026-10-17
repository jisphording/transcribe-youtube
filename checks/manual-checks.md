# Manual verification

The phase's same-user automated probe evidence is in the CLI contract. These procedures remain unverified for human/operator execution; they do not claim that launchd or actual note imports have passed.

### MC-01 — Subscription headless access under the backend user

**Claim**: The contained subscription probe returns valid JSON without API keys.
**Doc**: docs/antigravity-cli-contract.md
**Covers**: docs/antigravity-cli-contract.md
**Procedure**: Sign in interactively as the backend LaunchAgent user if needed; never export/copy tokens. Run the absolute CLI's --version and models commands. Use the contract's custom-agent file, instantiated Seatbelt profile, key-stripped environment and exact stdin/EOF invocation in a disposable cwd. Require explicit gemini-3.8-flash-medium, exit 0, terminal SUCCESS and valid JSON about the supplied text before the finite deadline; record CLI version and redacted results. Separately repeat from launchd during service validation; terminal success alone does not verify launchd.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: none

### MC-02 — Full-process containment and adversarial canaries

**Claim**: Session-local `manage_task` bookkeeping is permitted; only `Action=list` was tested.
**Doc**: docs/antigravity-cli-contract.md
**Covers**: docs/antigravity-cli-contract.md
**Procedure**: Instantiate the contract profile in a fresh directory with a custom agent, hook/MCP touch canaries and a separate disposable vault canary. Under the native-test profile (only its interpreter additionally permitted), require PermissionError for repo/vault/hook reads, workspace writes and child touch execution. Under the actual CLI profile, submit the adversarial file/shell/read request and manage_task list with EOF; require exit 0/SUCCESS, only session bookkeeping tool steps and absent write/shell/hook/MCP sentinels. Independently require successful harmless JSON generation under the same CLI policy. Verify the real resolved repo/vault paths are denied, including nonstandard locations, before production use. Do not send real vault contents or retry without containment.
**Last verified**: null
**Verified against**: null
**Status**: unverified
**Blocked reason**: none
