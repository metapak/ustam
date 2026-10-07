[English](README.md) | [Türkçe](README.tr.md)

<p align="center">
  <img src="docs/assets/cover-en.svg" alt="Illustrated orchestra stage with a conductor and specialist helpers" width="100%">
</p>

# Ustam

One local hub for Codex, Claude Code, OpenCode, and Antigravity.

This is the single maintained Ustam repository. Current downloads and releases live here; former provider-specific repositories link to this hub. [Repository migration](docs/repository-migration.md).

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/downloads/)

**Choose apps, projects, a chief, and specialist teams in one local browser page.**

Set up the team, check the changes, and see locally recorded past usage. The setup console runs on your computer. The chief coordinates and speaks with you; specialists do the assigned work. This division is an instruction rule, not a technical lock on the chief's tools.

> [!NOTE]
> This is an unofficial community project. It is not affiliated with or endorsed by OpenAI.

## Turkish introduction

[![Watch the Turkish Ustam introduction](docs/assets/ustam-trailer-poster-tr.png)](https://raw.githubusercontent.com/metapak/ustam/main/docs/assets/ustam-trailer-tr-55s.mp4)

[Play or download the Turkish video (MP4, 55 seconds)](https://raw.githubusercontent.com/metapak/ustam/main/docs/assets/ustam-trailer-tr-55s.mp4). Turkish titles with original music and sound effects; no spoken narration. Usage figures shown on screen are sample data. Select the cover or MP4 link to open the video.

## Antigravity

Antigravity supports project team setup with `inherit`, `flash` and `pro` model tiers, protected installation/restore and the manual Works bridge. Antigravity unattended Jobs and measured usage are unsupported; native read-only chief enforcement and model execution are unverified. [Support and limits](docs/antigravity.md).

## Download and start

**[Download Ustam for Mac arm64](https://github.com/metapak/ustam/releases/download/ustam-v1.0.0-beta.5/ustam-1.0.0-beta.5-macos-arm64.zip)** · [1.0.0-beta.5 release and checksums](https://github.com/metapak/ustam/releases/tag/ustam-v1.0.0-beta.5) · [Release notes](docs/release-ustam-v1.0.0-beta.5.md).

The Mac package includes Python and was checked locally. It has no Apple Developer ID signing or notarization; public-download Gatekeeper acceptance remains unresolved. [Mac installation notes](docs/ustam-macos-local-install.md).

Open Ustam, then follow **Project → Tool → Team → Install**:

1. **Project:** Add the project folder you want to configure.
2. **Tool:** Choose Codex, Claude Code, OpenCode or Antigravity for that project.
3. **Team:** Choose a ready or saved orchestra, or set the chief, helpers and supported model options.
4. **Install:** Preview the changes and confirm installation into the project. Installation does not start an AI task.

Install the selected provider's CLI separately; account login and model access are required for real provider work. Source use requires Python 3.11+. The [previous Windows/Linux beta.2 packages](https://github.com/metapak/ustam/releases/tag/ustam-v1.0.0-beta.2) cover the earlier three-tool version, without Antigravity or the newer features. They are legacy downloads, not beta.5 builds.

## Tool support

| Tool | Project teams and models | Restore | Recorded usage |
|---|---|---|---|
| Codex | Installed CLI and available model catalog | Managed configuration | Local recorded usage |
| Claude Code | Installed CLI; model-specific effort options | Managed configuration | Local recorded usage |
| OpenCode | Installed CLI and configured providers/models | Unavailable | Local records where supported |
| Antigravity | `agy` 1.2.16+ and local help check; `inherit`, `flash`, `pro` | Verified managed files and backups | Unsupported |

The manual Works bridge is available for all four tools. Antigravity unattended Jobs are disabled; account entitlement, native model execution and native read-only chief enforcement remain unverified. Available models and tools depend on the installed CLI and account. [Antigravity details](docs/antigravity.md) · [Works protocol](docs/ustam-work-protocol.md).

## Projects and orchestras

Add a project, select the provider you use there, then choose a saved orchestra or create one in that project's setup area. Review models and roles before saving. A saved orchestra is reusable configuration; saving it does not start a provider job. Review proposed project changes before applying them.

Configuration and previews can work offline. Starting real work requires the selected provider's CLI, login and model access. Team settings and the runtime's actual execution limits are separate; the page reports those limits. Usage/history describes recorded work, not a live orchestra animation.

## Advanced compatibility

The older provider-specific console remains available for existing projects. Its screenshots, ten-task presets and launchers are described separately in the [legacy reference](docs/legacy-console.md). Follow the [unified app guide](docs/ustam-hub.md) for current setup.

## License

[Apache-2.0](LICENSE) · [Attribution](NOTICE). This is an unofficial community project, not endorsed by provider maintainers.
