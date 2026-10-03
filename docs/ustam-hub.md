# Ustam local hub

Open Ustam → Select apps → Add projects → Review changes.

Ustam is one local browser hub for Codex, Claude Code and OpenCode. Select the apps you use, add project folders, then choose or create an orchestra in that project's setup area. Saving an orchestra saves reusable configuration; it does not start a provider job. Check the proposed project changes before applying them.

## Ready teams by work type

Choose the work type before a ready team. Every suggested team includes exploration, implementation, verification and review; these are helpers in addition to the chief.

| Work | Added specialist | Codex / Claude helpers | OpenCode helpers |
| --- | --- | --- | --- |
| Fix a bug | Failure analyst | 5 | 4 |
| Add a feature | — | 4 | 4 |
| Web | QA operator | 5 | 4 |
| Game | Researcher, QA operator | 6 | 4 |
| Backend | Advisor | 5 | 4 |
| Research | Researcher | 5 | 4 |
| Data | Researcher | 5 | 4 |
| Security | Researcher, advisor | 6 | 4 |

Suggestions use supported roles and models from the selected provider's catalog. An unavailable specialist can reduce the count; missing models require completion before saving. You can edit the team, and changing the work type does not silently overwrite your edits. These counts describe prepared configuration, not a promise that every helper runs simultaneously. OpenCode currently dispatches its four supported execution roles; the hub runs helpers one at a time.

## Native installation

Windows/Linux: extract the complete native ZIP from the beta.2 release, then open **Ustam.exe** or **Ustam**. Keep the extracted files together. The runtime is bundled; Python is not a separate requirement for native packages. The native Mac app is self-contained when built locally.

**Mac public download is not ready:** published beta.1/beta.2 Mac apps have unresolved first-launch problems. The unpublished beta.3 source candidate repairs package integrity, but this does not establish that a downloaded app opens. A local source build opened on the development Mac and the user confirmed seeing the page. Its quarantine attribute was naturally absent; no security protections were changed. The quarantine-marked downloaded test copy remained blocked, and macOS did not offer Open Anyway. Do not remove quarantine or disable Gatekeeper.

Apple Developer ID signing and notarization are unavailable. An ad-hoc code seal verifies package integrity; it does not confer Apple's trusted-distribution approval. Managed computers can impose additional restrictions.

## Local source installation on Mac

Download and extract the source ZIP from this repository. Source builds require Python 3.11+ and internet access to install the pinned PyInstaller build tool. Provider CLIs remain separate requirements.

The guided source installer is **under acceptance testing**. Its downloaded-source opening route has not yet been confirmed by a user. If you choose to test it, open `launchers/Install Ustam.applescript` as source in Apple's Script Editor, read the source, then click **Run** yourself. Select the extracted source folder containing `scripts`, `launchers` and `ustam`. The installer checks known Python installation paths; if Python is missing it offers the official Python download page. It builds in an isolated temporary environment, verifies pinned engines and all Mac native code, and installs into `~/Applications/Ustam.app`. An existing app is replaced only when identified as Ustam and is kept as a backup. Close a running Ustam copy before installing. Saved Ustam state and project configuration are not modified. Cancellation before replacement preserves the previous installed copy. Build logs remain in the temporary installer folder for troubleshooting.

For an advanced manual source build from the extracted repository:

```sh
python3 -m venv .venv-build
.venv-build/bin/python -m pip install pyinstaller==6.22.3
.venv-build/bin/python scripts/build_ustam_app.py --skip-engine-build
```

Open the resulting `dist/ustam-*/Ustam.app`. The checked-in immutable engine assets are verified; sibling repositories are unnecessary for this build. To run source directly instead, use `python3 -m ustam` with Python 3.11+. Each target OS and architecture needs its own build.

## Accounts, state and removal

Setup and previews can work offline. Starting work requires each selected provider's installed CLI, login and model access; API models can incur provider charges. Ustam does not supply accounts, credentials or a billing balance. History records local work; the orchestra animation is decorative.

Preferences, registered projects and orchestras live in Ustam's user state directory. Projects receive configuration only after explicit apply. Removing a project removes its Ustam registration, not its folder. Restore is separate and applies only to supported managed configuration; OpenCode restore is unavailable. The hub has no project uninstall action. Deleting Ustam.app does not uninstall project configuration or erase saved Ustam state.

Provider execution limits are reported separately from team configuration. Each provider uses a bundled pinned engine with exact file closure and SHA-256 verification. Native `ustam/VERSION` is separate from legacy engine versions. The UI launcher uses a console worker so frozen adapters retain JSON stdio; Windows workers are hidden. If automatic browser opening fails, Ustam shows the local address to open manually while the server continues running.

The [older provider console](legacy-console.md), screenshots, ten-task presets and project-first launchers are advanced compatibility references. They are separate from the current unified hub.
