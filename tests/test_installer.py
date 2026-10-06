from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.py"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
START_MARKER = "<!-- codex-bounded-orchestrator:start -->"
MANIFEST = Path(".codex/.bounded-orchestrator/install.json")

EXPECTED_ROLES = {
    "acceptance_test_author": ("acceptance-test-author.toml", "gpt-6.1-sol", "medium", "workspace-write"),
    "fast_lookup": ("fast-lookup.toml", "gpt-6-luna", "medium", "read-only"),
    "explorer": ("explorer.toml", "gpt-6-luna", "high", "read-only"),
    "researcher": ("researcher.toml", "gpt-6.1-sol", "medium", "read-only"),
    "implementer": (
        "implementer.toml",
        "gpt-6.1-sol",
        "high",
        "workspace-write",
    ),
    "verifier": ("verifier.toml", "gpt-6.1-sol", "high", "workspace-write"),
    "failure_analyst": (
        "failure-analyst.toml",
        "gpt-6.1-sol",
        "high",
        "read-only",
    ),
    "qa_operator": (
        "qa-operator.toml",
        "gpt-6.1-sol",
        "medium",
        "workspace-write",
    ),
    "reviewer": ("reviewer.toml", "gpt-6-astra", "medium", "read-only"),
    "advisor": ("advisor.toml", "gpt-6-astra", "xhigh", "read-only"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_installer_module():
    spec = importlib.util.spec_from_file_location("bounded_install", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNINSTALL_SUPPORTED = load_installer_module().SAFE_UNINSTALL_SUPPORTED

class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.target = Path(self.temporary.name) / "target repository"
        self.target.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @unittest.skipIf(UNINSTALL_SUPPORTED, 'platform supports safe uninstall')
    def test_uninstall_refuses_unsupported_platform_without_changes(self) -> None:
        installed = self.run_installer('--profile', 'astra')
        self.assertEqual(installed.returncode, 0, installed.stderr)
        manifest = self.target / MANIFEST
        original = manifest.read_bytes()
        result = self.run_installer('--uninstall')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Safe uninstall requires POSIX', result.stderr)
        self.assertEqual(manifest.read_bytes(), original)

    def run_installer(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.pop("ANTHROPIC_API_KEY", None)
        environment.pop("DEEPSEEK_API_KEY", None)
        return subprocess.run(
            [sys.executable, str(INSTALLER), str(self.target), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            input=input_text,
            env=environment,
        )

    def test_managed_block_defers_model_routing_to_registered_project_roles(self) -> None:
        # Each preset can select different models; prose must not override them.
        for preset in ("balanced", "quality", "economy", "quota-saver", "custom"):
            with self.subTest(preset=preset):
                result = self.run_installer("--preset", preset)
                self.assertEqual(result.returncode, 0, result.stderr)
                block = (self.target / "AGENTS.md").read_text(encoding="utf-8")
                self.assertIn("configured chief/root only talks", block)
                self.assertIn("`.codex/config.toml`", block)
                self.assertIn("registered `.codex/agents/*.toml` file", block)
                self.assertIn("authoritative for routing", block)
                self.assertIn("coordination-only regardless of its configured model", block)
                self.assertNotIn("Use Luna only", block)
                self.assertNotIn("Terra for exploration", block)

    def test_fresh_astra_install_has_exact_routing(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)

        config_path = self.target / ".codex/config.toml"
        config = read_toml(config_path)
        self.assertEqual(config["model"], "gpt-6-astra")
        self.assertEqual(config["model_reasoning_effort"], "medium")
        self.assertEqual(config["review_model"], "gpt-6-astra")
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(config["agents"]["max_depth"], 1)

        registered_roles = {
            name for name, value in config["agents"].items() if isinstance(value, dict)
        }
        self.assertEqual(registered_roles, set(EXPECTED_ROLES))

        for role_name, (filename, model, effort, sandbox) in EXPECTED_ROLES.items():
            registration = config["agents"][role_name]
            self.assertEqual(registration["config_file"], f"./agents/{filename}")
            role = read_toml(self.target / ".codex/agents" / filename)
            self.assertEqual(role["name"], role_name)
            self.assertEqual(role["model"], model)
            self.assertEqual(role["model_reasoning_effort"], effort)
            self.assertEqual(role["sandbox_mode"], sandbox)
            self.assertIs(role["agents"]["enabled"], False)

        self.assertTrue(
            (self.target / ".agents/skills/bounded-orchestrator/SKILL.md").is_file()
        )
        self.assertTrue((self.target / ".codex/tools/candidate.py").is_file())
        self.assertTrue((self.target / ".codex/tools/ledger.py").is_file())
        if os.name != "nt":
            self.assertTrue(
                (self.target / ".codex/tools/ledger.py").stat().st_mode & 0o111
            )
        self.assertTrue(
            (
                self.target
                / ".agents/skills/bounded-orchestrator-ui-design/SKILL.md"
            ).is_file()
        )
        self.assertTrue(
            (
                self.target
                / ".agents/skills/bounded-orchestrator-security-review/SKILL.md"
            ).is_file()
        )
        self.assertIn(START_MARKER, (self.target / "AGENTS.md").read_text())

        manifest = json.loads((self.target / MANIFEST).read_text())
        self.assertEqual(manifest["profile"], "astra")
        self.assertEqual(manifest["tool_version"], VERSION)
        self.assertTrue(manifest["files"][".codex/config.toml"]["owned"])

    def test_cli_install_refuses_manifest_leaf_symlink_before_writes(self) -> None:
        manifest = self.target / MANIFEST
        manifest.parent.mkdir(parents=True)
        outside = Path(self.temporary.name) / "private-install.json"
        outside.write_text('{"schema": 1, "files": {}, "agents_block": false}\n')
        manifest.symlink_to(outside)
        before = outside.read_bytes()
        result = self.run_installer("--preset", "balanced")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())
        self.assertTrue(manifest.is_symlink())
        self.assertEqual(outside.read_bytes(), before)
        self.assertFalse((self.target / ".codex/config.toml").exists())
        self.assertFalse((self.target / ".codex/agents").exists())
        self.assertFalse((self.target / ".codex/.bounded-orchestrator/.gitignore").exists())

    def test_crlf_managed_copy_and_generated_text_keep_ownership_hashes(self) -> None:
        installer = load_installer_module()
        source = Path(self.temporary.name) / "source"
        runtime = Path(".codex/.bounded-orchestrator/.gitignore")
        agent = Path(".codex/agents/team-slot-50.toml")
        (source / runtime).parent.mkdir(parents=True)
        (source / runtime).write_bytes(b"# private\r\n*\r\n")
        manifest = {"files": {}}
        installer.install_file(root=source, target=self.target, relative=runtime,
                               manifest=manifest, force=False, dry_run=False, messages=[])
        installer.install_text_file(target=self.target, relative=agent,
                                    text='model = "gpt-6-sol"\r\n', manifest=manifest,
                                    force=False, dry_run=False, messages=[])
        for relative, expected in ((runtime, b"# private\r\n*\r\n"),
                                   (agent, b'model = "gpt-6-sol"\r\n')):
            path = self.target / relative
            self.assertEqual(path.read_bytes(), expected)
            self.assertTrue(installer.unchanged_owned(manifest, relative, path))

    def test_sol_fallback_profile_keeps_terra_sol_astra_routing(self) -> None:
        result = self.run_installer("--profile", "sol")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = read_toml(self.target / ".codex/config.toml")
        self.assertEqual(config["model"], "gpt-6.1-sol")
        self.assertEqual(config["model_reasoning_effort"], "high")
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(
            read_toml(self.target / ".codex/agents/reviewer.toml")["model"],
            "gpt-6-astra",
        )
        self.assertEqual(
            read_toml(self.target / ".codex/agents/fast-lookup.toml")["model"],
            "gpt-6-luna",
        )

    def test_install_is_idempotent_and_preserves_existing_agents_text(self) -> None:
        (self.target / "AGENTS.md").write_text(
            "# Existing instructions\n", encoding="utf-8"
        )
        first = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stderr)
        text = (self.target / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Existing instructions"))
        self.assertEqual(text.count(START_MARKER), 1)

    def test_existing_config_is_preserved_and_example_is_written(self) -> None:
        codex = self.target / ".codex"
        codex.mkdir()
        existing = (
            'model = "custom-model"\n'
            "[mcp_servers.example]\n"
            'url = "https://example.com"\n'
        )
        config = codex / "config.toml"
        config.write_text(existing, encoding="utf-8")
        before = digest(config)

        result = self.run_installer("--profile", "sol")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(digest(config), before)
        example = codex / "bounded-orchestrator.config.example.toml"
        self.assertTrue(example.is_file())
        parsed = read_toml(example)
        self.assertEqual(parsed["model"], "gpt-6.1-sol")
        self.assertEqual(parsed["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(
            parsed["agents"]["reviewer"]["config_file"],
            "./agents/reviewer.toml",
        )
        manifest = json.loads((self.target / MANIFEST).read_text())
        self.assertTrue(
            manifest["files"][".codex/bounded-orchestrator.config.example.toml"][
                "owned"
            ]
        )

    def test_deepseek_selection_preserves_existing_mcp_config(self) -> None:
        codex = self.target / ".codex"
        codex.mkdir()
        config = codex / "config.toml"
        existing = (
            'model = "gpt-local"\n'
            "[mcp_servers.example]\n"
            'url = "https://example.com"\n'
        )
        config.write_text(existing, encoding="utf-8")

        result = self.run_installer("--external-provider", "deepseek")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), existing)
        example = read_toml(codex / "bounded-orchestrator.config.example.toml")
        self.assertIn("deepseek_proposals", example["mcp_servers"])

    def test_force_config_creates_ignored_backup_and_replaces(self) -> None:
        codex = self.target / ".codex"
        codex.mkdir()
        config = codex / "config.toml"
        config.write_text('model = "custom"\n', encoding="utf-8")

        result = self.run_installer("--profile", "sol", "--force-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = read_toml(config)
        self.assertEqual(parsed["model"], "gpt-6.1-sol")
        self.assertEqual(parsed["model_reasoning_effort"], "high")
        backups = list(
            (self.target / ".codex/.bounded-orchestrator/backups").rglob(
                "config.toml"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertIn('model = "custom"', backups[0].read_text())

    def test_conflicting_agent_is_preserved_without_force(self) -> None:
        path = self.target / ".codex/agents/reviewer.toml"
        path.parent.mkdir(parents=True)
        path.write_text("custom reviewer\n", encoding="utf-8")
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_text(), "custom reviewer\n")
        self.assertIn(f"SKIP {Path('.codex/agents/reviewer.toml')}", result.stdout)

    def test_force_replaces_conflicting_agent_with_backup(self) -> None:
        path = self.target / ".codex/agents/reviewer.toml"
        path.parent.mkdir(parents=True)
        path.write_text("custom reviewer\n", encoding="utf-8")
        result = self.run_installer("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = read_toml(path)
        self.assertEqual(parsed["name"], "reviewer")
        self.assertEqual(parsed["model_reasoning_effort"], "medium")
        backups = list(
            (self.target / ".codex/.bounded-orchestrator/backups").rglob(
                "reviewer.toml"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "custom reviewer\n")

    @unittest.skipUnless(UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_uninstall_removes_owned_files_and_block(self) -> None:
        (self.target / "AGENTS.md").write_text("# Keep me\n", encoding="utf-8")
        self.assertEqual(self.run_installer().returncode, 0)
        result = self.run_installer("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.target / ".codex/config.toml").exists())
        self.assertFalse((self.target / ".codex/agents/reviewer.toml").exists())
        self.assertFalse((self.target / ".codex/agents/fast-lookup.toml").exists())
        self.assertFalse((self.target / ".codex/tools/ledger.py").exists())
        self.assertFalse(
            (
                self.target
                / ".agents/skills/bounded-orchestrator-ui-design/SKILL.md"
            ).exists()
        )
        self.assertFalse(
            (
                self.target
                / ".agents/skills/bounded-orchestrator-security-review/SKILL.md"
            ).exists()
        )
        self.assertEqual((self.target / "AGENTS.md").read_text(), "# Keep me\n")
        self.assertFalse((self.target / MANIFEST).exists())

    @unittest.skipUnless(UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_uninstall_keeps_modified_managed_file(self) -> None:
        self.assertEqual(self.run_installer().returncode, 0)
        path = self.target / ".codex/agents/reviewer.toml"
        path.write_text(path.read_text() + "# local edit\n", encoding="utf-8")
        result = self.run_installer("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(path.exists())
        self.assertIn("modified after installation", result.stdout)

    @unittest.skipUnless(UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_uninstall_rejects_symlink_ancestor_before_any_removal(self) -> None:
        self.assertEqual(self.run_installer().returncode, 0)
        agents = self.target / ".codex/agents"
        outside = Path(self.temporary.name) / "outside-agents"
        agents.rename(outside)
        agents.symlink_to(outside, target_is_directory=True)
        reviewer = outside / "reviewer.toml"
        before = reviewer.read_bytes()
        manifest = self.target / MANIFEST
        manifest_before = manifest.read_bytes()
        result = self.run_installer("--uninstall")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink uninstall path", result.stderr)
        self.assertEqual(reviewer.read_bytes(), before)
        self.assertEqual(manifest.read_bytes(), manifest_before)

    def test_dry_run_writes_nothing(self) -> None:
        result = self.run_installer("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_quality_and_economy_presets_change_role_routing(self) -> None:
        result = self.run_installer("--preset", "quality")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_toml(self.target / ".codex/config.toml")["model_reasoning_effort"], "high")
        self.assertEqual(read_toml(self.target / ".codex/agents/implementer.toml")["model"], "gpt-6-astra")
        result = self.run_installer("--preset", "economy", "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_toml(self.target / ".codex/config.toml")["model"], "gpt-6.1-sol")
        self.assertEqual(read_toml(self.target / ".codex/agents/fast-lookup.toml")["model_reasoning_effort"], "low")

    def test_custom_role_model_and_effort_overrides(self) -> None:
        result = self.run_installer(
            "--preset", "custom",
            "--role-model", "implementer=gpt-custom",
            "--role-effort", "implementer=xhigh",
            "--role-model", "owner=gpt-owner",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_toml(self.target / ".codex/config.toml")["model"], "gpt-owner")
        role = read_toml(self.target / ".codex/agents/implementer.toml")
        self.assertEqual((role["model"], role["model_reasoning_effort"]), ("gpt-custom", "xhigh"))

    def test_interactive_custom_selection(self) -> None:
        # custom, then model+effort for owner and nine roles, then no external provider
        answers = ["4"]
        for role, (_, model, effort, _) in [("owner", ("", "gpt-owner", "high", ""))]:
            answers.extend([model, effort])
        for role_name in EXPECTED_ROLES:
            answers.extend(["", ""])
        answers.append("1")
        result = self.run_installer("--interactive", input_text="\n".join(answers) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NATIVE PROFILE / YEREL PROFIL", result.stdout)
        self.assertEqual(read_toml(self.target / ".codex/config.toml")["model"], "gpt-owner")

    def test_interactive_output_survives_restrictive_cp1252_console(self) -> None:
        environment = dict(os.environ)
        environment.pop("ANTHROPIC_API_KEY", None)
        environment.pop("DEEPSEEK_API_KEY", None)
        environment["PYTHONIOENCODING"] = "cp1252:strict"
        result = subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                str(self.target),
                "--interactive",
                "--dry-run",
            ],
            input=b"1\n1\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("cp1252"))
        self.assertIn(b"NATIVE PROFILE", result.stdout)
        self.assertIn(b"REVIEW / SON KONTROL", result.stdout)
        self.assertIn(b"OpenAI GPT", result.stdout)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_console_fallback_configures_stdout_and_stderr(self) -> None:
        module = load_installer_module()
        stdout_bytes = io.BytesIO()
        stderr_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252", errors="strict")
        stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252", errors="strict")
        with patch.object(module.sys, "stdout", stdout), patch.object(
            module.sys, "stderr", stderr
        ):
            module.configure_console_output()
            stdout.write("ş")
            stderr.write("ı")
            stdout.flush()
            stderr.flush()
        self.assertEqual(stdout_bytes.getvalue(), b"?")
        self.assertEqual(stderr_bytes.getvalue(), b"?")

    def test_external_anthropic_bridge_is_opt_in_and_never_persists_key(self) -> None:
        result = self.run_installer(
            "--external-provider", "anthropic",
            "--external-model", "claude-opus-5-5",
            "--external-effort", "xhigh",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        bridge = self.target / ".codex/tools/anthropic_mcp.py"
        self.assertTrue(bridge.is_file())
        config_text = (self.target / ".codex/config.toml").read_text()
        config = read_toml(self.target / ".codex/config.toml")
        server = config["mcp_servers"]["anthropic_claude"]
        self.assertEqual(server["env_vars"], ["ANTHROPIC_API_KEY"])
        self.assertIn("claude-opus-5-5", server["args"])
        manifest_text = (self.target / MANIFEST).read_text()
        self.assertNotIn("test-only-key", config_text + manifest_text)
        self.assertIn("ANTHROPIC_API_KEY is not set", result.stdout)

    def test_haiku_bridge_uses_auto_without_unsupported_effort(self) -> None:
        result = self.run_installer(
            '--external-provider', 'anthropic',
            '--external-model', 'claude-haiku-4-5-20251001',
            '--external-effort', 'auto',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        server = read_toml(self.target / '.codex/config.toml')['mcp_servers']['anthropic_claude']
        self.assertEqual(server['args'][-2:], ['--effort', 'auto'])
        invalid = self.run_installer(
            '--external-provider', 'anthropic',
            '--external-model', 'claude-haiku-4-5-20251001',
            '--external-effort', 'high',
        )
        self.assertEqual(invalid.returncode, 2)

    def test_native_roles_reject_external_brand_models(self) -> None:
        for model in ("claude-sonnet-5-5", "deepseek-flash"):
            with self.subTest(model=model):
                result = self.run_installer(
                    "--preset", "custom", "--role-model", f"implementer={model}"
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("must be an OpenAI GPT model ID", result.stderr)
                self.assertIn("--external-provider", result.stderr)

    def test_default_install_has_no_external_provider(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = read_toml(self.target / ".codex/config.toml")
        self.assertNotIn("mcp_servers", config)
        manifest = json.loads((self.target / MANIFEST).read_text())
        self.assertEqual(manifest["external_provider"], "none")
        self.assertFalse((self.target / ".codex/tools/anthropic_mcp.py").exists())
        self.assertFalse((self.target / ".codex/tools/deepseek_mcp.py").exists())

    def test_external_deepseek_bridge_is_opt_in_and_never_persists_key(self) -> None:
        result = self.run_installer(
            "--external-provider", "deepseek",
            "--external-model", "deepseek-flash",
            "--external-effort", "max",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        bridge = self.target / ".codex/tools/deepseek_mcp.py"
        self.assertTrue(bridge.is_file())
        config_text = (self.target / ".codex/config.toml").read_text()
        config = read_toml(self.target / ".codex/config.toml")
        server = config["mcp_servers"]["deepseek_proposals"]
        self.assertEqual(server["env_vars"], ["DEEPSEEK_API_KEY"])
        self.assertIn("deepseek-flash", server["args"])
        manifest_text = (self.target / MANIFEST).read_text()
        self.assertNotIn("test-only-key", config_text + manifest_text)
        self.assertIn("DEEPSEEK_API_KEY is not set", result.stdout)

    def test_external_provider_model_and_effort_are_provider_specific(self) -> None:
        wrong_model = self.run_installer(
            "--external-provider", "deepseek", "--external-model", "claude-sonnet-5-5"
        )
        self.assertEqual(wrong_model.returncode, 2)
        self.assertIn("deepseek-", wrong_model.stderr)
        wrong_effort = self.run_installer(
            "--external-provider", "anthropic", "--external-effort", "minimal"
        )
        self.assertEqual(wrong_effort.returncode, 2)
        self.assertIn("Unsupported anthropic effort", wrong_effort.stderr)

    def test_switching_external_provider_removes_previous_owned_bridge(self) -> None:
        first = self.run_installer("--external-provider", "anthropic")
        self.assertEqual(first.returncode, 0, first.stderr)
        result = self.run_installer("--external-provider", "deepseek")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.target / ".codex/tools/anthropic_mcp.py").exists())
        self.assertTrue((self.target / ".codex/tools/deepseek_mcp.py").is_file())
        config = read_toml(self.target / ".codex/config.toml")
        self.assertNotIn("anthropic_claude", config.get("mcp_servers", {}))
        self.assertIn("deepseek_proposals", config["mcp_servers"])

    def test_modified_deepseek_config_keeps_active_bridge_when_anthropic_requested(self) -> None:
        first = self.run_installer("--external-provider", "deepseek")
        self.assertEqual(first.returncode, 0, first.stderr)
        config_path = self.target / ".codex/config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\n# user setting\n",
            encoding="utf-8",
        )

        result = self.run_installer("--external-provider", "anthropic")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / ".codex/tools/deepseek_mcp.py").is_file())
        self.assertTrue((self.target / ".codex/tools/anthropic_mcp.py").is_file())
        active = read_toml(config_path)
        pending = read_toml(
            self.target / ".codex/bounded-orchestrator.config.example.toml"
        )
        self.assertIn("deepseek_proposals", active["mcp_servers"])
        self.assertIn("anthropic_claude", pending["mcp_servers"])
        self.assertIn(f"KEEP {Path('.codex/tools/deepseek_mcp.py')}", result.stdout)
        self.assertIn("Requested external: anthropic", result.stdout)
        self.assertIn("Active external   : deepseek", result.stdout)

    def test_modified_deepseek_config_keeps_active_bridge_when_none_requested(self) -> None:
        first = self.run_installer("--external-provider", "deepseek")
        self.assertEqual(first.returncode, 0, first.stderr)
        config_path = self.target / ".codex/config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\n# user setting\n",
            encoding="utf-8",
        )

        result = self.run_installer("--external-provider", "none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / ".codex/tools/deepseek_mcp.py").is_file())
        active = read_toml(config_path)
        pending = read_toml(
            self.target / ".codex/bounded-orchestrator.config.example.toml"
        )
        self.assertIn("deepseek_proposals", active["mcp_servers"])
        self.assertNotIn("mcp_servers", pending)
        self.assertIn(f"KEEP {Path('.codex/tools/deepseek_mcp.py')}", result.stdout)
        self.assertIn("Requested external: none", result.stdout)
        self.assertIn("Active external   : deepseek", result.stdout)

    @unittest.skipUnless(UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_uninstall_ignores_manifest_path_outside_allowlist(self) -> None:
        self.assertEqual(self.run_installer().returncode, 0)
        outside = self.target.parent / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        manifest_path = self.target / MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["../outside.txt"] = {
            "owned": True,
            "sha256": digest(outside),
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = self.run_installer("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), "keep")
        self.assertIn("KEEP invalid manifest path", result.stdout)

    def test_disabling_external_provider_removes_owned_bridge_and_mcp_config(self) -> None:
        self.assertEqual(
            self.run_installer("--external-provider", "anthropic").returncode, 0
        )
        self.assertTrue((self.target / ".codex/tools/anthropic_mcp.py").is_file())
        result = self.run_installer("--external-provider", "none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.target / ".codex/tools/anthropic_mcp.py").exists())
        config = read_toml(self.target / ".codex/config.toml")
        self.assertNotIn("mcp_servers", config)

    @unittest.skipUnless(UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_external_install_uninstall_removes_bridge(self) -> None:
        self.assertEqual(
            self.run_installer("--external-provider", "anthropic").returncode, 0
        )
        result = self.run_installer("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.target / ".codex/tools/anthropic_mcp.py").exists())

    @unittest.skipUnless(UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_deepseek_install_uninstall_removes_bridge(self) -> None:
        self.assertEqual(
            self.run_installer("--external-provider", "deepseek").returncode, 0
        )
        result = self.run_installer("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.target / ".codex/tools/deepseek_mcp.py").exists())


if __name__ == "__main__":
    unittest.main()
