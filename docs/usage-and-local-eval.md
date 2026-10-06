[English](usage-and-local-eval.md) | [Türkçe](usage-and-local-eval.tr.md)

# Usage reporting and optional local evaluation

## Locally observed Codex usage

```bash
python3 .codex/tools/usage_report.py
python3 .codex/tools/usage_report.py --json
```

The reporter scans `~/.codex/sessions` read-only and sums observed request `token_usage_record.usage` counters; legacy `token_count` cumulative counters use chronological deltas. It never prints prompt or source content. Model, role, and thread are shown only when those fields are present on token/session metadata (thread filename is a fallback). The totals are local observations; they are not quota percentages, bills, or cost estimates.

Ustam collects usage for the selected project across its recorded session history; the run selector narrows it to one recorded root session and its explicitly linked helpers. Project filtering indexes all context and request project metadata before excluding unrelated session files; later project changes and explicit request overrides remain eligible. Collection has a 30-second scan deadline and a bounded in-memory cache invalidated by file identity, size, and modification time; it writes no cache into projects or session folders. Usage refresh has a local loading indicator, cancellation, and an 80-second total browser deadline. Only an unfinished history-index deadline triggers exactly one automatic continuation; other errors never retry automatically. The observed 24 GB archive required about 49 seconds across two bounded attempts; a later refresh took about 8 seconds. Errors preserve the last successful measurements and allow retry.

Ustam’s period filter offers All time, Last 7 days, Last 30 days, and an inclusive custom range. Seven and thirty days mean local calendar days including today, in the browser’s time zone; the end boundary is the following local midnight, including daylight-saving changes. Dates filter already normalized accounting records, so earlier cumulative baselines and request-record precedence remain intact. Changing dates, work, metric, or group reuses the loaded report and does not collect usage again. Undated records stay in All time and are excluded from bounded ranges with an explicit count; sources without dated details cannot provide range totals.

All team, Chiefs, Helpers, and Unclassified cards show the selected metric for the selected project, tool, work, and period. Each card selects that group in the detail panel; individual recorded identities remain selectable. Cache is displayed separately and is never added a second time to Codex or Claude totals. OpenCode totals retain its source convention of summing observed input, output, reasoning, cache read, and cache write once each. Counts are unique recorded session identities, not people working at the same time. Category membership requires explicit topology or recognized role metadata; names and models never determine a role. Usage that cannot be assigned to a category stays Unclassified.

OpenCode uses the existing project-scoped sanitized export collector, with its documented recent-session limit (up to 12 listed sessions); these are observed records, not complete account history. Claude retains its current explicit-metric/no-data behavior; Ustam does not activate telemetry or invent agent identities.

## Explicit local evaluation

Copy `.codex/bounded-orchestrator.eval.example.json` to a project-local manifest and edit its explicit `argv` array. Nothing runs automatically.

```bash
python3 .codex/tools/local_eval.py .codex/local-eval.json
python3 .codex/tools/ledger.py require-eval --label focused-tests
python3 .codex/tools/ledger.py ready-for-review
```

The runner does not use a shell, runs in the project root, enforces a bounded timeout, and writes an ignored summary. By default the summary stores an output digest, not command output. This is a project-specific check, not a universal quality benchmark. Once opted in for a run, a passing summary is required before review.
The pass summary is bound to a privacy-safe fingerprint of HEAD plus relevant tracked and untracked worktree content. Any later candidate change makes that pass stale; ignored evaluation summaries are excluded so saving the result does not invalidate itself.

The ledger also records stable attempt and event IDs. `interrupt`, `wait-user`, and `needs-repair` preserve the last short evidence; `retry --evidence ...` permits one bounded retry and routes repair back to the named owner role.


[Request accounting, filters and local console](local-console.md). `payload.usage` is per request; only legacy `token_count.info.total_token_usage` uses cumulative deltas.

Usage starts at the verified managed installation for the selected project and provider. The private baseline receipt is separate from project/orchestra metadata. Later successful orchestra changes retain this start; restoring to an uninstalled project ends the generation. Legacy timestamps use the current owned manifest and, only when its digest matches the immediate restore receipt, the earlier owned manifest. Missing provenance shows an unverified-start note and no historical measurements. Filtering follows cumulative accounting and excludes undated, pre-installation and future records; calendar filters reuse this result. A provider apply that succeeds before private receipt persistence fails is reported explicitly; Ustam does not claim rollback.
