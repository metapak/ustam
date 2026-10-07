# Ustam 1.0.0-beta.5

[English](release-ustam-v1.0.0-beta.5.md) · [Türkçe](release-ustam-v1.0.0-beta.5.tr.md)

Ustam now has one maintained repository: [metapak/ustam](https://github.com/metapak/ustam). Codex, Claude Code, OpenCode and Antigravity share one hub, current download and release page. The existing Codex repository is renamed to preserve its history, issues, stars and previous releases. Former Claude/OpenCode repositories retain their history with migration notices and archive status.

## What changes

- All four engines are bundled from 144 source-only runtime files in this repository, with original licenses, attribution and source provenance. Every engine is pinned to the same immutable primary source snapshot. Building the checked-in closure requires no sibling checkout or external GitHub engine fetch.
- Project tool selection offers all four providers while keeping the global Codex-specific preference separate. Ready, custom and saved orchestras continue through Project → Tool → Team → Install.
- Claude model/effort choices follow each actual model's supported values. Unsupported Haiku and unresolved-alias effort controls are hidden and their values remain empty. The chief excludes max effort; helpers offer it only when the model supports it. Invalid legacy combinations require explicit correction before save/preview.
- The latest approved provider picker and model/effort fixes are included. The existing Turkish trailer, cover and all earlier releases are preserved.

## Installation and support

The new native package is macOS arm64 only. It includes the local runtime and pinned engines; provider CLIs, accounts and model access are separate requirements. Windows/Linux beta.2 downloads remain historical packages for the older three-tool version; they do not include Antigravity or the newer features.

Mac Developer ID signing/notarization and public-download Gatekeeper acceptance remain unresolved. No Windows runtime acceptance or paid model execution is claimed for this release. The exact source/native validation and asset checksums must match the final publication candidate; local verification is limited to the documented environments.

Antigravity retains the manual Works bridge and protected project installation/restore. It requires `agy` 1.2.16+ and a local help check, supports inherit/flash/pro model tiers, and has no measured usage or unattended Jobs. Native read-only chief enforcement and account/model execution remain unverified. Existing local state and project configurations are not automatically migrated or overwritten by the repository rename.

## Repository transition

The former Codex URL redirects to the renamed repository; do not recreate that old name. Archived Claude/OpenCode README links provide navigation to Ustam. Older release assets remain available. Beta.5 is a prerelease: use its explicit release/download link rather than GitHub's generic latest-release URL, which can resolve to an older stable release. [Migration details](repository-migration.md).
