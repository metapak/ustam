# Changelog

## Ustam 1.0.0-beta.3 — 2026-10-06

Current shared hub, bounded manual work protocol, acceptance-test author, ownership/rollback fixes, provider parity, cached usage, locale/pending UI, SVG icons, Mac lifecycle/package improvements and Turkish trailer. [Full English notes](docs/release-ustam-v1.0.0-beta.3.md) · [Türkçe sürüm notları](docs/release-ustam-v1.0.0-beta.3.tr.md). Mac arm64 package only; Windows/Linux remain beta.2.

## 0.6.0

- Added truthful local Codex usage reporting, a quota-saver profile, explicit local evaluation, and richer retry-aware task history.

All notable changes to the public project are documented here. This repository begins a new publication history from the recovered v0.2.0 source archive; see [source provenance](docs/provenance.md).

## [0.5.0] - 2026-09-14

### Added

- Added an opt-in, standard-library DeepSeek proposal bridge using the current `deepseek-flash` V4.1 Flash API alias.
- Added a three-step guided terminal display with plain-ASCII sections, short choice explanations, a final configuration review, clearer results, and next steps.
- Added mocked DeepSeek MCP/API tests and provider-switch lifecycle coverage.

### Changed

- Native Codex roles, including custom profiles, now accept only forward-compatible OpenAI `gpt-*` IDs. Anthropic and DeepSeek models are available only through explicit external API proposal providers.
- External providers now default visibly to none and share the same proposal-only, no-workspace-access, environment-key boundary.

### Safety

- Neither `ANTHROPIC_API_KEY` nor `DEEPSEEK_API_KEY` is written to project files, install metadata, or output.
- Switching or disabling providers removes only unchanged installer-owned bridge files and preserves user-modified config unless forced with backup.
- When a user-modified active config is preserved, bridges it still references are retained; output separates the requested provider from the currently active provider.

## [0.4.1] - 2026-09-10

### Fixed

- Interactive Turkish installer output no longer crashes Windows consoles that use restrictive encodings such as cp1252. Standard output and error keep their active encoding and replace only unsupported characters; UTF-8 consoles preserve the original Turkish text.
- Added a regression test that runs the interactive flow with strict cp1252 output streams.

## [0.4.0] - 2026-09-10

### Added

- Turkish-friendly one-click selection for `balanced`, `quality`, `economy`, and `custom` routing presets.
- Per-role model and effort choices in custom setup, with equivalent non-interactive flags.
- An opt-in, dependency-free stdio MCP bridge for bounded Claude implementation proposals through the Anthropic Messages API.
- English and Turkish external-provider guidance and v0.4.0 release notes.

### Safety

- The Claude bridge cannot read or write the workspace; the native implementer remains the sole writer.
- `ANTHROPIC_API_KEY` is inherited only from the environment and is never stored in config or install metadata.
- Uninstall ignores manifest paths outside the fixed managed-file allowlist.
- Tests cover the MCP handshake and a mocked provider HTTP request; no live paid API call is claimed.

## [0.3.0] - 2026-09-10

### Added

- A standard-library local task ledger with validated IDs, dependencies, state transitions, human and JSON status, and review/completion gates.
- Atomic, restrictive storage for metadata-only run state under the existing ignored runtime directory.
- Opt-in UI design and security review expertise packs that preserve the base authority and bounded-workflow rules.
- English and Turkish guides for the ledger, expertise packs, and v0.3.0 release.

### Changed

- The base skill and managed `AGENTS.md` block now describe ledger use and expertise-pack boundaries.
- Install, uninstall, validation, and release packaging include the ledger and both expertise packs.
- Release tests derive artifact names from `VERSION`.
- Ledger atomic writes explicitly close temporary file descriptors before replacement for Windows compatibility.

### Limits

- The ledger detects unresolved declared work; it cannot prove that all necessary work was declared or that completed work is correct.
- Expertise packs provide instructions, not enforcement, permissions, credentials, or external-action authority.

## [0.2.0] - 2026-09-10

### Added

- Project-scoped Astra-owner and Sol-owner profiles.
- Explicit profiles for explorer, researcher, implementer, verifier, failure analyst, QA operator, reviewer, advisor, and optional fast lookup roles.
- A bounded orchestration skill with task contracts, one-writer ownership, candidate freezing, independent review, and finite repair/re-review budgets.
- A dependency-free candidate hash and Git identity tool.
- A safe installer with dry-run, conflict preservation, local backups for forced replacement, repeatable installation, and ownership-aware uninstall.
- Supplied launchers for macOS, Windows, and POSIX shells, plus release archive construction.
- English-first and Turkish documentation, examples, FAQ, roadmap, contribution guidance, security reporting, and GitHub templates.

### Routing

- GPT-6 Astra medium owns scope, architecture, routing, integration, triage, and final outcome by default.
- GPT-5.6 Terra medium handles repository exploration and technical research.
- GPT-5.6 Sol high is the single writer for bounded implementation scopes and the read-only failure analyst after evidence identifies a blocker.
- GPT-5.6 Terra high independently verifies acceptance criteria.
- GPT-6 Astra medium reviews one frozen candidate and returns findings only.
- GPT-5.6 Luna medium remains an optional exact, read-only lookup path.

### Attribution

- Preserved Apache-2.0 licensing and attribution to [donvito/codex-astra-luna-orchestrator](https://github.com/donvito/codex-astra-luna-orchestrator) in [LICENSE](LICENSE), [NOTICE](NOTICE), and the [design comparison](docs/from-astra-luna-orchestrator.md).

[0.2.0]: https://github.com/metapak/ustam-codex-orchestrator/releases/tag/v0.2.0
[0.3.0]: https://github.com/metapak/ustam-codex-orchestrator/releases/tag/v0.3.0
[0.4.0]: https://github.com/metapak/ustam-codex-orchestrator/releases/tag/v0.4.0
[0.4.1]: https://github.com/metapak/ustam-codex-orchestrator/releases/tag/v0.4.1
[0.5.0]: https://github.com/metapak/ustam-codex-orchestrator/releases/tag/v0.5.0

### Publication validation repair / Yayın doğrulama düzeltmesi

- Windows skips the POSIX-only filesystem executable-bit check; regression coverage preserves macOS/Linux checks and ZIP mode checks.
- Windows, yalnızca POSIX dosya sistemine ait çalıştırma izni kontrolünü atlar; macOS/Linux ve ZIP izin kontrolleri korunur.
