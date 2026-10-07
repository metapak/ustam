---
name: bounded-orchestrator
description: Coordinate all OpenCode execution work with bounded roles, one writer per scope, frozen candidates, finite review loops, local task history, and optional candidate-bound evaluation.
---

# Ustam

Use this workflow for every execution request, including a trivial edit, lookup, or check. The owner handles only conversation, planning, delegation, brief worker reports, decisions, and redelegation.

1. The owner defines acceptance criteria; a specialist operates the dependency-aware ledger.
2. Explorer or researcher gathers missing evidence without writing.
3. The owner assigns one implementer exclusive ownership of each write scope.
4. The implementer changes only that scope and runs focused checks.
5. Assign a specialist to freeze the exact candidate with `.opencode/tools/candidate.py freeze`.
6. The verifier checks that candidate. A failure routes through the owner; the verifier never repairs it.
7. Assign a specialist to run a configured shell-free local evaluation when the ledger requires one.
8. The reviewer independently reviews the same candidate. Assign a specialist to freeze again after any repair.
9. Finish only when all required ledger work and required evaluation pass.

Every child must deny `subagent`. The owner never reads source, researches, edits, builds, tests, freezes, reviews, or operates the ledger directly, even for small tasks. If delegation is unavailable, report the blocker and do not run execution as fallback. Review loops are finite: after two failed repair attempts, stop and report the evidence. Do not store prompts, source, transcripts, credentials, or secrets in runtime metadata.

OpenCode V2 does not document a numeric subagent-depth or parallelism setting. The source template permits nine named specialists; a selected helper roster replaces the owner allowlist with those named slots. Every child denies `subagent`. A fixture with three helpers is an example, not a cap. Default to one specialist; use independent parallel specialists only when justified and bounded by the runtime.

See [task contract](references/task-contract.md), [review protocol](references/review-protocol.md), and [escalation](references/escalation.md).


Default to one suitable specialist. Parallel specialists require independent scopes and a stated latency or context benefit; never fill capacity for its own sake. Reuse a relevant specialist session when the runtime exposes continuation, and start fresh when unrelated context or independence requires it.

Send a scoped brief with only objective, owned paths, constraints, acceptance criteria, evidence pointers, and stop conditions. Avoid full transcript forwarding. An independent reviewer receives the frozen candidate and acceptance criteria, not the implementer's reasoning history. Preserve one writer per scope.

Wait for events or completion within a bounded interval supported by OpenCode. Do not repeatedly poll unchanged status. On timeout, request one compact evidence checkpoint and decide whether to continue, narrow, or stop. Return a compact report: changes, owned paths, actual checks/results, acceptance status, and remaining decisions. Context and report length are preferences; steps bound model turns, not tokens.
