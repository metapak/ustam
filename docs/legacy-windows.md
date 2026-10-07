> Advanced compatibility only / Yalnız ileri düzey uyumluluk. These instructions describe the older provider-specific console, not the unified Ustam app. / Bu yönergeler birleşik Ustam uygulamasını değil eski sağlayıcı konsolunu anlatır.


# Install on Windows

## Four steps on Windows

Have Codex, [Git](https://git-scm.com/downloads), and [Python 3.11 or newer](https://www.python.org/downloads/) installed. Python is not included.

1. **Download:** [Get the current ZIP](https://github.com/metapak/ustam/archive/refs/heads/main.zip) and open the extracted folder.
2. **Open:** Open `launchers` and double-click **Launch Ustam.vbs**.
3. **Choose a project:** Pick the Git project folder where you use Codex.
4. **Install:** In the browser, keep the suggested team or change it. Click **Check changes**, then **Install**. Restart Codex in that project.

For later changes, reopen the launcher and click **Save**; no uninstall is needed. An already-open Codex session may need to be reopened before it uses the changes. Double-click behavior has not been tested on every Windows setup. See the [local console guide](local-console.md) for more help.

## Remove a setup on Windows

Automatic **Uninstall setup** and `--uninstall` are unavailable on Windows because this Python build cannot perform the directory-handle checks required for safe deletion. The browser disables the button, and the CLI refuses before changing project files.

To remove the setup manually, close Codex and other programs editing the project. In the extracted installer folder, run `py -3 scripts/install.py "C:\path\to\project" --uninstall --dry-run` and check the project path and each `REMOVE`/`KEEP` line. In File Explorer, delete only the listed `REMOVE` files that still match that review. Leave every `KEEP` file untouched. In the project's `AGENTS.md`, remove only the text between `<!-- codex-bounded-orchestrator:start -->` and `<!-- codex-bounded-orchestrator:end -->` if the block is still unchanged; keep all other text. Keep `.codex\.bounded-orchestrator\backups`, usage records, and its `.gitignore` so private backups remain ignored by Git. Delete `.codex\.bounded-orchestrator\install.json` last, after checking the remaining files. If any file changed since the dry run, repeat it before deleting that file.

Check the **Selected project** path near the top of the browser. To use the suggested team, select **Continue with this team · review changes**, then **Install**. Changing the team is optional. External API models are under **Optional advisers**. Settings controls pause while an operation runs, and a visible result appears when it finishes.

<details>
<summary>Terminal alternative</summary>

Double-click `setup.cmd` to use the older guided terminal installer. It does not offer the per-slot browser team builder. It preserves an existing `.codex\config.toml` by default and shows its choices before writing.

## PowerShell path

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

Non-interactive:

```powershell
.\scripts\install.ps1 -Target "C:\path\to\project" -Preset balanced
```

Preview:

```powershell
.\scripts\install.ps1 -Target "C:\path\to\project" -Preset balanced -DryRun
```

For Claude proposals, set `ANTHROPIC_API_KEY` and select `anthropic`. For DeepSeek proposals, set `DEEPSEEK_API_KEY` and select `deepseek`. Keys are not written by the installer. See [docs/external-providers.md](external-providers.md).

The terminal launcher detects `py -3`, `python`, or `python3`. Python 3.11 or newer is required by the installer, browser console, candidate fingerprint tool, and local task ledger.
After installation, restart Codex and run the read-only checklist in [docs/runtime-smoke-test.md](runtime-smoke-test.md).

</details>
