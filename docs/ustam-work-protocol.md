# Local work protocol

Install the selected orchestra into a registered project, then write your task once in Codex, Claude Code or OpenCode. Installation never starts a paid job. Works displays explicitly published local records, not inferred chats or sessions. Existing usage and installation metadata remain separate.

The installed `.<provider>/tools/work_protocol.py` is a manual native bridge. Packaged macOS/Linux installs provide `sh .<provider>/tools/work_protocol ACTION --operation-id unique-id --json request.json`, bound to the installer runtime and project. The native worker also accepts `--work-protocol PROVIDER ACTION --project /absolute/project`; Windows uses this fixed argv entrypoint. Source installs can invoke the Python tool using `ACTION --project /absolute/project --operation-id unique-id --json request.json`; use `python` on Windows. `list` needs no request or operation ID. The captured runtime, Git with an existing commit and native shell permission must be available. If any is missing, report waiting/unsupported. There is no automatic provider hook or paid controller call.

For `create`, the request is a short contract:

```json
{"contract":{"scope":"Write a JSON result","criteria":["Result is the agreed object"],"files":["result.json"],"commands":[]}}
```

The tool derives the provider from its installed location and resolves the project against Ustam's canonical registry. It returns a work ID and version; subsequent requests include `id` and `version`. Routine scope inherits the limited standing policy for isolated preparation and built-in artifact checks. This is not evidence of human contract approval. Material scope revisions and command extensions require one trusted Works approval bound to the displayed contract. Native CLI actor strings and JSON flags cannot grant that approval.

A separate acceptance-test-author role publishes `test_pack` before production implementation:

```json
{"id":"WORK_ID","version":1,"pack":{"author":"acceptance_test_author","checks":[{"criterion":0,"kind":"json_equals","path":"result.json","expected":{"ok":true}}],"good":{"result.json":"{\"ok\":true}"},"bad":[{"result.json":"{\"ok\":false}"}]}}
```

Every criterion needs coverage. `contains` and `json_equals` are supported deterministic artifact checks. The runner evaluates the good reference and each bad example in temporary directories; a weak pack that passes a bad example is rejected. Browser behavior, performance and semantic research quality are unsupported check kinds. The role declaration is native-reported provenance; the observed evidence is that the local controller evaluated the pack before opening production implementation. Existing independent verifier roles remain read-only.

`prepare` creates a detached Git worktree in private Ustam state and snapshots current tracked, staged, unstaged and non-ignored untracked context. It never initializes Git, resets, cleans or commits user files. Checkout filters requiring executable commands are unsupported. Implement inside the returned workspace and only within the contract's file ownership. `freeze` binds exact content, file modes, index, HEAD, contract and test pack. Concurrent active ownership overlaps are refused.

`question` accepts a bounded question, optional string options, and affected work IDs in the same project/provider. Identity includes normalized wording, options and task versions. All affected tasks wait for one scoped Works answer. A changed version makes the answer stale. Answering does not launch a model or start unrelated work.

`check` with `role:"verifier"` evaluates the frozen candidate and every approved required command. Arbitrary commands, missing executables and candidate modifications cannot produce a passing receipt. Exact argv, executable hash, candidate/contract/pack/policy hashes, lock/config hash, sanitized environment key names and exit codes identify controller-observed evidence. Tool versions are explicitly unknown when no separate version command was authorized. `reported_check` records unverified helper-reported evidence and cannot authorize apply. No raw environment, credentials, provider prompt or command output is retained. These local process observations do not attest to a person's or model's identity.

`integration_check` builds a temporary combined tree from the current project context plus the owned candidate edits. It reruns artifact checks and every required command. Conflicting user edits stop integration. The receipt binds current main snapshot, candidate, contract, policy and pack. If main or candidate changes, revalidation is required. Works offers one explicit `apply` approval bound to that receipt; no automatic merge, commit or push occurs. User staged changes stay staged, and existing worktrees remain available for inspection. A worktree is not a security sandbox: commands still need explicit authorization and a fixed local environment/cwd. No network isolation is claimed.

Records live under the private Ustam state directory in `works/works.json` with schema version 1, atomic writes and a stable cross-process lock. Unknown versions are refused without rewriting them. Each operation ID binds one action/input; a duplicate returns its saved result. Interrupted intent is retained and never replayed automatically. `cancel` and `resume` preserve candidates. Partial preparation requires inspection before a new work can be published. Interrupted apply may leave partial user-approved edits; its intent blocks replay and the candidate remains available. This protocol is recoverable, not a cross-filesystem transaction.

GET `/api/works` returns only records for registered projects. POST accepts trusted UI `approve`, `answer`, `apply`, `cancel` and `resume` actions under existing exact Host/Origin/CSRF rules. It revalidates the registered canonical project path. Native tools publish operational records; they cannot invoke approval endpoints by supplying a trust flag. Local metadata remains editable by the same OS user, so these are control boundaries, not signed attestations.
