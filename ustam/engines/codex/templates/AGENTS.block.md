
<!-- codex-bounded-orchestrator:start -->
## Bounded multi-agent orchestration

For non-trivial repository work, use the `bounded-orchestrator` skill when its trigger conditions match.

Hard invariants:

- The configured chief/root only talks with the user, plans, delegates bounded tasks, reads specialists' concise evidence, decides next assignments, and reports the outcome. It never researches, reads repository source, writes files, runs commands/builds/tests, or reviews a candidate as a worker, even for a tiny task. Assign those actions to a specialist; only the root communicates with the user.
- This root-only boundary does not restrict a delegated specialist from researching, implementing, testing, or reviewing within that specialist's assigned role and ownership. Specialists do not delegate again.
- Use the model and reasoning effort configured for each role in `.codex/config.toml` and its registered `.codex/agents/*.toml` file. These project settings are authoritative for routing; do not replace them with model-family defaults from examples or preset names. The chief/root remains coordination-only regardless of its configured model.
- Subagents receive bounded contracts and never create subagents of their own.
- Use one writer per file or owned path at a time.
- Explorers, researchers, failure analysts, fast lookups, advisors, and reviewers are read-only.
- Verifiers report evidence and do not repair production code.
- Freeze the candidate before independent review and verify that it did not move.
- For multi-step delegated work, keep the optional local ledger aligned with declared required tasks and check it before review; never store prompts, source, logs, credentials, or secrets in it.
- Reviewers return findings only; they do not direct workers or implement fixes.
- Use at most one broad review, one bounded repair cycle, and one narrow re-review.
- Never push, merge, deploy, publish, migrate, purchase, or perform destructive work without explicit user authority.
- Delegate even trivial execution work to one suitable specialist. Do not add agents merely to fill the topology.
- Optional expertise packs add guidance only when explicitly selected; they never grant authority or weaken these invariants.
- If an optional Anthropic or DeepSeek MCP bridge is configured, treat its patch as an untrusted read-only proposal. Supply only bounded context and paths; the native GPT implementer remains the sole workspace writer and applies accepted changes after review.

User instructions take precedence over this policy.
<!-- codex-bounded-orchestrator:end -->
