> Advanced compatibility only / Yalnız ileri düzey uyumluluk. These instructions describe the older provider-specific console, not the unified Ustam app. / Bu yönergeler birleşik Ustam uygulamasını değil eski sağlayıcı konsolunu anlatır.


# Install on macOS

## Four steps on Mac

Have Codex, [Git](https://git-scm.com/downloads), and [Python 3.11 or newer](https://www.python.org/downloads/) installed. Python is not included.

1. **Download:** [Get the current ZIP](https://github.com/metapak/ustam/archive/refs/heads/main.zip) and open the extracted folder.
2. **Open:** Open `launchers` and double-click **Ustam.app**.
3. **Follow the two folder steps:** If macOS asks for the setup package, read the first dialog and choose the outer folder extracted from the ZIP whose name starts with `ustam-codex-orchestrator`. It contains `launchers` and `scripts`; the picker opens in Downloads. Choose **Try again** if you select a different folder. The second dialog asks for the Git project where you work with Codex; setup will save settings there. Each picker has a short prompt in your system language.
4. **Install:** In the browser, keep the suggested team or change it. Click **Check changes**, then **Install**. Restart Codex in that project.

For later changes, reopen the app and click **Save**; no uninstall is needed. An already-open Codex session may need to be reopened before it uses the changes. If macOS blocks the unsigned app, Control-click it and choose **Open**. Keep the app inside the extracted folder: it needs the neighboring `launch_dashboard.py` and `scripts` files. If no folder picker appears, check whether macOS is still showing a security prompt for the app. If the browser cannot open, the launcher shows an alert with the local address to open manually. See the [local console guide](local-console.md) for more help.

Check the **Selected project** path near the top of the browser. To use the suggested team, select **Continue with this team · review changes**, then **Install**. Changing the team is optional. External API models are under **Optional advisers**. Settings controls pause while an operation runs, and a visible result appears when it finishes.

<details>
<summary>Terminal alternative</summary>

Double-click `setup.command` to use the older guided terminal installer. It does not offer the per-slot browser team builder. It preserves an existing `.codex/config.toml` by default and shows its choices before writing.

```bash
chmod +x setup.command scripts/install.sh
./setup.command
```

If you already know the target path, this also opens the interactive profile, model, and effort selector:

```bash
./setup.command /absolute/path/to/project
```

When explicit options are present, `setup.command` passes them through unchanged. For example, the following remains non-interactive:

```bash
./setup.command /absolute/path/to/project --preset economy --dry-run
```

Non-interactive:

```bash
./scripts/install.sh /absolute/path/to/project --preset balanced
```

Preview:

```bash
./scripts/install.sh /absolute/path/to/project --preset balanced --dry-run
```

For Claude proposals, export `ANTHROPIC_API_KEY` and select `anthropic`. For DeepSeek proposals, export `DEEPSEEK_API_KEY` and select `deepseek`. Keys are inherited from the environment and are not written by the installer. See [docs/external-providers.md](external-providers.md).

Python 3.11 or newer is required by the installer, browser console, candidate fingerprint tool, and local task ledger.
After installation, restart Codex and run the read-only checklist in [docs/runtime-smoke-test.md](runtime-smoke-test.md).

</details>
