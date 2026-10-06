<!-- claude-bounded-orchestrator:start -->
## Bounded orchestration

Delegate every execution task, including small ones, to the project agents in `.claude/agents/`. The main Claude session speaks with the user, plans, decides scope and architecture, routes work, reads brief evidence reports, integrates decisions, triages findings, and reports the final outcome. It must not inspect source, research, implement, run checks, or independently review a candidate. If delegation is unavailable, report that blocker instead of taking over execution. This is a behavioral instruction, not a runtime tool-access boundary. Only `implementer` writes, with one writer per explicitly assigned file or scope. Children must not delegate.
The main-session restrictions in the preceding paragraph do not apply to delegated project agents; they perform their explicitly assigned work under their own contracts.

Every delegated request must name its objective, exact scope, write ownership or read-only status, relevant context, invariants, deliverable, acceptance criteria, and stop conditions. Agents return evidence to the main session and do not own the final result.

Before independent review, stop all writers and freeze the candidate by recording an exact commit or deterministic worktree identity. Give the reviewer that identity and reject its review if the candidate changes. The reviewer returns findings only and must not contact the implementer.

Allow at most one focused repair for a proven verification failure and at most one focused repair for accepted material review findings. Do not retry the same failed contract without new evidence or a narrower method.

For workflows with three or more dependent steps, use `.claude/tools/task_ledger.py`. The ledger stores short metadata only and must never contain prompts, source code, command output, logs, credentials, personal data, or secrets. Do not claim completion until all required agents have stopped, ledger dependencies are complete, `check` passes, the final candidate still matches the reviewed identity, accepted findings are resolved or disclosed, and the highest-value checks pass.

External effects require the user's exact authority. Optional `/ui-design` and `/secure-change` skills add guidance only when explicitly invoked; they do not grant tools or permissions.

Native owner and child-agent routing uses only Anthropic Claude model aliases or full `claude-*` IDs. OpenAI, DeepSeek, and other brands are external API providers, never native agents.

If the optional `openai_bounded_implementation` or `deepseek_bounded_proposal` MCP tool is configured, treat it as a proposal-only external source. Give it an exact task, repository-relative allowed paths, reviewed context, constraints, and acceptance criteria. It cannot inspect or write the workspace. The native `implementer` remains the sole writer: it must review the returned patch, reject out-of-scope changes, apply only accepted edits, and run the normal verification and frozen-review flow. Never include credentials or unrelated source in the supplied context.

Use one specialist by default, even for small execution tasks. Parallelize only scopes that are independent and explain why overlap saves time; concurrency is a ceiling, not a target. Reuse or resume an existing suitable agent when the runtime supports it. Send the smallest sufficient brief: exact allowed paths, acceptance checks, policy invariants and relevant facts; avoid full conversation history, repeated file dumps, secrets and unrelated logs. Independent reviewers receive a fresh brief with the frozen identity, requirements and verification evidence, without implementer discussion or conclusions. Use bounded event waits, back off when status is unchanged, and do not repeatedly poll identical state. Require short evidence reports with changed files, checks actually run, acceptance status and blockers. Report length, retry and context preferences are prompt guidance, never hard token limits.

### Ustam local work protocol / Ustam yerel is protokolu
For a project registered in Ustam, delegate protocol operations to the appropriate bounded helper.
On macOS/Linux use the installed fixed bridge: `sh .claude/tools/work_protocol ACTION --operation-id UNIQUE_ID --json REQUEST.json`. It captures the installer runtime and project. On Windows invoke the installed Ustam worker executable with `--work-protocol claude ACTION --project PROJECT --operation-id UNIQUE_ID --json REQUEST.json`; source installs may use `python .claude/tools/work_protocol.py`.
Write the task once in this native chat. Installation never starts a model job. There is no automatic hook guarantee.
Publish a short contract via create: scope, criteria array, exclusive relative files, and explicit command argv arrays.
A separate acceptance-test-author publishes test_pack (author, checks, good, bad) before production implementation.
Each check names criterion index, kind (contains or json_equals), path, expected. Quality uses isolated temporary examples.
Then prepare returns a detached workspace snapshot preserving dirty user context. Implement ONLY inside that workspace.
Freeze before verifier check; reported_check is unverified self-report. Real check receipts are generated by this local runner.
Question records deduplicate affected task versions and options. Human scope extensions, answers and one final apply approval
use Ustam Works. Native helpers cannot manufacture these approvals. Commands outside trusted approved argv require attention.
Use integration_check after passing checks; it rechecks the combined current project tree before user-approved apply.
Cancel/resume preserve the candidate. Reuse one operation ID only for the same request; interrupted operations need inspection.
No reset, automatic merge, commit, push, dependency install or permission widening. Worktrees are not security sandboxes.
If the captured runtime, CLI shell permission, Git, or a supported check type is unavailable, report waiting/unsupported; never invent a pass.
Bu aracta gorevi bir kez yazin. Test yazari uygulamadan once paketi yayinlar; dogrulayici salt okunur kalir.
Kararlar ve son uygulama Ustam Isler ekranindan onaylanir. Kurulum yapay zeka cagrisi baslatmaz.

<!-- claude-bounded-orchestrator:end -->
