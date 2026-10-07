# Ustam 1.0.0-beta.4

[English](release-ustam-v1.0.0-beta.4.md) · [Türkçe](release-ustam-v1.0.0-beta.4.tr.md)

Antigravity joins Codex, Claude Code and OpenCode in the same local Ustam hub. This prerelease adds project-scoped Antigravity team setup, protected installation/restore and a manual Works bridge. The existing Turkish introduction and cover are retained.

## Antigravity teams and models

Select Antigravity for registered projects and choose a ready team, a saved custom team or your own helper/model settings. Supported native model tiers are `inherit`, `flash` and `pro`; per-role effort settings are unavailable. A saved concurrency of one is Ustam's compatibility policy, not proof of a native concurrency cap or a read-only chief.

Setup requires the supported `agy` CLI, version 1.2.16 or newer, and a successful local help capability check. Missing, older, unknown or incompatible CLIs return a reason before project files are written. CLI detection does not verify account entitlement or model execution. Legacy IDE availability is shown separately as unverified. Installation does not sign in, install Antigravity, change global settings or start an AI task.

## Protected project installation and restore

Preview binds the team, current file bytes, project directory identity and a five-minute expiry. Identity is checked again before apply and on restore. A copied installation cannot authorize writes in a replacement directory.

The installer owns a finite provider namespace, preserves existing AGENTS.md/GEMINI.md files and other providers, refuses unowned collisions and checks the CLI before writing. Durable before/after backups and completed-step receipts report preflight, backup, applied and verified stages. Restore validates backup integrity and expected current bytes before recovering the previous installation or removing the first installation.

Symlinks, changed managed files and incomplete installations are refused. Apply and rollback compare expected bytes. Concurrent user edits are preserved and reported; backups remain available for inspection. Rollback is called complete only after all original byte states are observed again. A failed transaction with retained conflicts requires recovery inspection.

## Manual Works bridge

Antigravity uses the existing bounded Works workflow: scope and success criteria, separate acceptance-test author before implementation, isolated Git worktree, frozen candidate, controller-observed checks, independent read-only review, integration recheck and explicit human apply. The project bridge captures the provider, registered project and trusted runtime. Its native parser cannot approve, answer or apply work. Source Python invocation requires isolated mode (`-I`).

Rules and role instructions are prompt conventions. They do not enforce native tool permissions, a security sandbox or a read-only chief. A CLI exit code or helper's reported success cannot replace observed checks and human approval. Native model execution is not verified.

## Explicitly unsupported surfaces

Antigravity unattended Jobs are disabled. Usage is reported as unsupported with empty records and totals; this is not measured zero usage. Ustam does not infer usage from IDE logs, arbitrary transcripts or global quota, and installs no global statusline collector. The engine is included in the three existing public repositories; there is no fourth public repository.

## Downloads and validation

The new native asset is macOS arm64 only. Windows/Linux native downloads remain the previous beta.2 version. Source archives are tied to the published beta.4 commit. The Mac arm64 bundle was built and checked locally: all three repository suites and validators passed, along with native packaging/lifecycle checks and the manual Works bridge for all four providers. Seven tests were skipped in each repository suite. This does not establish public-download acceptance or native model execution. The Antigravity engine is pinned to the internal source at commit `80613c6c7c11d2a3398b3499a402f328575f0fe0`; release checksums identify the exact downloadable files.

Apple Developer ID signing/notarization and public-download Gatekeeper acceptance remain unresolved. No real Windows runtime QA or paid model call is claimed. Local tests and transaction receipts do not guarantee that every environment or interrupted filesystem operation will succeed.
