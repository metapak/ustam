#!/usr/bin/env python3
"""Safely install Ustam into an existing repository."""

from __future__ import annotations

import argparse
import model_capabilities
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import subprocess
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
START_MARKER = "<!-- claude-bounded-orchestrator:start -->"
END_MARKER = "<!-- claude-bounded-orchestrator:end -->"
MANIFEST_RELATIVE = Path(".claude/.bounded-orchestrator/install.json")
BACKUP_RELATIVE = Path(".claude/.bounded-orchestrator/backups")
SETTINGS_RELATIVE = Path(".claude/settings.json")
SETTINGS_EXAMPLE_RELATIVE = Path(".claude/bounded-orchestrator.settings.example.json")
MCP_RELATIVE = Path(".mcp.json")
MCP_EXAMPLE_RELATIVE = Path(".claude/bounded-orchestrator.mcp.example.json")
DEEPSEEK_MCP_EXAMPLE_RELATIVE = Path(".claude/bounded-orchestrator.deepseek.mcp.example.json")
OPENAI_BRIDGE_RELATIVE = Path(".claude/tools/openai_mcp.py")
DEEPSEEK_BRIDGE_RELATIVE = Path(".claude/tools/deepseek_mcp.py")
MCP_SERVER_NAME = "openai-bounded-implementer"
DEEPSEEK_MCP_SERVER_NAME = "deepseek-bounded-proposal"
MODEL_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
CLAUDE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
CLAUDE_OWNER_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
OPENAI_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
DEEPSEEK_EFFORTS = frozenset({"low", "high", "max"})
EXTERNAL_PROVIDERS = {
    "openai": {
        "label": "OpenAI GPT",
        "bridge": OPENAI_BRIDGE_RELATIVE,
        "example": MCP_EXAMPLE_RELATIVE,
        "server": MCP_SERVER_NAME,
        "default_model": "gpt-5.6-sol",
        "default_effort": "high",
        "efforts": OPENAI_EFFORTS,
        "key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek V4.1 Flash",
        "bridge": DEEPSEEK_BRIDGE_RELATIVE,
        "example": DEEPSEEK_MCP_EXAMPLE_RELATIVE,
        "server": DEEPSEEK_MCP_SERVER_NAME,
        "default_model": "deepseek-flash",
        "default_effort": "high",
        "efforts": DEEPSEEK_EFFORTS,
        "key": "DEEPSEEK_API_KEY",
    },
}
ROLES = (
    "owner",
    "explorer",
    "researcher",
    "acceptance-test-author",
    "implementer",
    "verifier",
    "failure-analyst",
    "qa-operator",
    "reviewer",
    "advisor",
)
ROLE_LABELS = {
    "owner": "owner / ana yonetici",
    "explorer": "explorer / inceleyici",
    "researcher": "researcher / arastirmaci",
    "acceptance-test-author": "acceptance test author / kabul testi yazari",
    "implementer": "implementer / uygulayici",
    "verifier": "verifier / kontrolcu",
    "failure-analyst": "failure analyst / hata cozumleyici",
    "qa-operator": "QA operator / kullanim kontrolcusu",
    "reviewer": "reviewer / son inceleyici",
    "advisor": "advisor / danisman",
}
PRESETS = {
    "balanced": {
        "owner": ("claude-opus-5-5", "xhigh"),
        "explorer": ("claude-sonnet-5-5", "medium"),
        "researcher": ("claude-sonnet-5-5", "medium"),
        "implementer": ("claude-sonnet-5-5", "high"),
        "verifier": ("claude-sonnet-5-5", "high"),
        "failure-analyst": ("claude-opus-5-5", "high"),
        "qa-operator": ("claude-sonnet-5-5", "high"),
        "reviewer": ("claude-opus-5-5", "high"),
        "advisor": ("claude-opus-5-5", "xhigh"),
    },
    "quality": {
        "owner": ("claude-opus-5-5", "xhigh"),
        "explorer": ("claude-opus-5-5", "high"),
        "researcher": ("claude-opus-5-5", "high"),
        "implementer": ("claude-opus-5-5", "xhigh"),
        "verifier": ("claude-opus-5-5", "xhigh"),
        "failure-analyst": ("claude-opus-5-5", "xhigh"),
        "qa-operator": ("claude-opus-5-5", "high"),
        "reviewer": ("claude-opus-5-5", "xhigh"),
        "advisor": ("claude-opus-5-5", "xhigh"),
    },
    "economy": {
        "owner": ("claude-sonnet-5-5", "medium"),
        "explorer": ("claude-sonnet-5-5", "low"),
        "researcher": ("claude-sonnet-5-5", "low"),
        "implementer": ("claude-sonnet-5-5", "medium"),
        "verifier": ("claude-sonnet-5-5", "medium"),
        "failure-analyst": ("claude-sonnet-5-5", "medium"),
        "qa-operator": ("claude-sonnet-5-5", "medium"),
        "reviewer": ("claude-sonnet-5-5", "medium"),
        "advisor": ("claude-sonnet-5-5", "high"),
    },
    "quota-saver": {
        "owner": ("claude-sonnet-5-5", "low"),
        "explorer": ("claude-sonnet-5-5", "low"),
        "researcher": ("claude-sonnet-5-5", "low"),
        "implementer": ("claude-sonnet-5-5", "medium"),
        "verifier": ("claude-sonnet-5-5", "medium"),
        "failure-analyst": ("claude-sonnet-5-5", "medium"),
        "qa-operator": ("claude-sonnet-5-5", "medium"),
        "reviewer": ("claude-sonnet-5-5", "medium"),
        "advisor": ("claude-sonnet-5-5", "medium"),
    },
}
for _profile in PRESETS.values():
    _profile["acceptance-test-author"] = ("claude-sonnet-5-5", "medium")

BASE_MANAGED_FILES = (
    Path(".claude/agents/acceptance-test-author.md"),
    Path(".claude/tools/work_protocol.py"),
    Path(".claude/tools/work_protocol_core.py"),
    Path(".claude/tools/work_protocol"),
    Path(".claude/agents/explorer.md"),
    Path(".claude/agents/researcher.md"),
    Path(".claude/agents/implementer.md"),
    Path(".claude/agents/verifier.md"),
    Path(".claude/agents/failure-analyst.md"),
    Path(".claude/agents/qa-operator.md"),
    Path(".claude/agents/reviewer.md"),
    Path(".claude/agents/advisor.md"),
    Path(".claude/skills/ui-design/SKILL.md"),
    Path(".claude/skills/secure-change/SKILL.md"),
    Path(".claude/tools/task_ledger.py"),
    Path(".claude/tools/usage_report.py"),
    Path(".claude/tools/console_settings.py"),
    Path(".claude/tools/console/index.html"),
    Path(".claude/tools/console/app.js"),
    Path(".claude/tools/console/style.css"),
    Path(".claude/tools/console/orchestra.svg"),
    Path(".claude/tools/local_eval.py"),
    Path(".claude/bounded-orchestrator.eval.example.json"),
    Path(".claude/.bounded-orchestrator/.gitignore"),
)
OPTIONAL_MANAGED_FILES = (
    OPENAI_BRIDGE_RELATIVE,
    DEEPSEEK_BRIDGE_RELATIVE,
    MCP_EXAMPLE_RELATIVE,
    DEEPSEEK_MCP_EXAMPLE_RELATIVE,
)
RUNTIME_IGNORE_RELATIVE = Path(".claude/.bounded-orchestrator/.gitignore")
ALLOWED_UNINSTALL_FILES = frozenset(
    path.as_posix()
    for path in (*BASE_MANAGED_FILES, *OPTIONAL_MANAGED_FILES, SETTINGS_RELATIVE, SETTINGS_EXAMPLE_RELATIVE)
)
ROSTER_AGENT_PATH = re.compile(r"\.claude/agents/orchestra-slot-(?:0[1-9]|[1-9][0-9])\.md\Z")
SECURE_UNINSTALL_DIR_FD = all(
    function in getattr(os, "supports_dir_fd", ())
    for function in (os.open, os.rename, os.unlink, os.link, os.mkdir, os.stat)
)


def work_protocol_wrapper(target):
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--work-protocol', 'claude']
    else:
        command = [sys.executable, str((target / '.claude/tools/work_protocol.py').resolve())]
    command += ['--project', str(target.resolve())]
    return '#!/bin/sh\n# Fixed project and installer runtime; no paid call.\nexec ' + ' '.join(shlex.quote(a) for a in command) + ' "$@"\n'


class InstallError(RuntimeError):
    """Expected installer failure."""


class PartialUninstallError(InstallError):
    """An uninstall error after a file mutation may require manual review."""


def configure_stdio() -> None:
    """Keep prompts usable when a terminal cannot encode Turkish characters."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise InstallError("VERSION is missing") from exc


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def same_file(left: Path, right: Path) -> bool:
    return left.is_file() and right.is_file() and digest(left) == digest(right)


def same_text(path: Path, content: str) -> bool:
    try:
        return path.is_file() and path.read_text(encoding="utf-8") == content
    except (OSError, UnicodeDecodeError):
        return False


def atomic_copy(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_text(destination: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_manifest(target: Path) -> dict[str, Any]:
    path = target / MANIFEST_RELATIVE
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise InstallError(f"unsafe install manifest: {path}")
    if not path.exists():
        return {"schema": SCHEMA_VERSION, "files": {}, "claude_block": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read install manifest: {path}") from exc
    if data.get("schema") != SCHEMA_VERSION or not isinstance(data.get("files"), dict):
        raise InstallError(f"unsupported install manifest: {path}")
    return data


def remember(manifest: dict[str, Any], relative: Path, destination: Path, owned: bool) -> None:
    manifest.setdefault("files", {})[relative.as_posix()] = {"sha256": digest(destination), "owned": owned}


def backup(target: Path, destination: Path, dry_run: bool) -> Path:
    relative = destination.relative_to(target)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = target / BACKUP_RELATIVE / stamp / relative
    index = 1
    while candidate.exists():
        candidate = candidate.with_name(f"{destination.name}.{index}")
        index += 1
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(target):
        raise InstallError("backup path escapes target")
    if not dry_run:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, candidate)
    return candidate


def ensure_sources(root: Path) -> None:
    required = [root / item for item in (*BASE_MANAGED_FILES, OPENAI_BRIDGE_RELATIVE, DEEPSEEK_BRIDGE_RELATIVE)]
    required += [root / SETTINGS_RELATIVE, root / "templates/CLAUDE.block.md", root / "VERSION"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise InstallError("installer source is incomplete:\n  - " + "\n  - ".join(missing))


def install_one(root: Path, target: Path, relative: Path, manifest: dict[str, Any], force: bool, dry_run: bool, output: list[str], content: str | None = None) -> None:
    source = root / relative
    destination = target / relative
    previous = manifest.get("files", {}).get(relative.as_posix(), {})
    if destination.exists() and destination.is_dir():
        output.append(f"SKIP {relative}: destination is a directory")
    elif not destination.exists():
        output.append(f"INSTALL {relative}")
        if content is None:
            atomic_copy(source, destination, dry_run)
        else:
            atomic_text(destination, content, dry_run)
        if not dry_run:
            remember(manifest, relative, destination, True)
    elif (same_text(destination, content) if content is not None else same_file(source, destination)):
        output.append(f"UNCHANGED {relative}")
        if not dry_run:
            remember(manifest, relative, destination, bool(previous.get("owned")))
    elif not force:
        output.append(f"SKIP {relative}: existing file differs; use --force to replace")
    else:
        saved = backup(target, destination, dry_run)
        output.append(f"BACKUP {relative} -> {saved.relative_to(target)}")
        output.append(f"REPLACE {relative}")
        if content is None:
            atomic_copy(source, destination, dry_run)
        else:
            atomic_text(destination, content, dry_run)
        if not dry_run:
            remember(manifest, relative, destination, True)


def install_settings(root: Path, target: Path, manifest: dict[str, Any], force_settings: bool, dry_run: bool, output: list[str], content: str) -> None:
    destination = target / SETTINGS_RELATIVE
    if not destination.exists():
        output.append(f"INSTALL {SETTINGS_RELATIVE}")
        atomic_text(destination, content, dry_run)
        if not dry_run:
            remember(manifest, SETTINGS_RELATIVE, destination, True)
        return
    if same_text(destination, content):
        previous = manifest.get("files", {}).get(SETTINGS_RELATIVE.as_posix(), {})
        output.append(f"UNCHANGED {SETTINGS_RELATIVE}")
        if not dry_run:
            remember(manifest, SETTINGS_RELATIVE, destination, bool(previous.get("owned")))
        return
    if force_settings:
        saved = backup(target, destination, dry_run)
        output.append(f"BACKUP {SETTINGS_RELATIVE} -> {saved.relative_to(target)}")
        output.append(f"REPLACE {SETTINGS_RELATIVE}")
        atomic_text(destination, content, dry_run)
        if not dry_run:
            remember(manifest, SETTINGS_RELATIVE, destination, True)
        return
    output.append(f"PRESERVE {SETTINGS_RELATIVE}")
    output.append(f"INSTALL {SETTINGS_EXAMPLE_RELATIVE} (merge manually)")
    example = target / SETTINGS_EXAMPLE_RELATIVE
    if example.exists() and not same_text(example, content) and not dry_run:
        output.append(f"SKIP {SETTINGS_EXAMPLE_RELATIVE}: existing example differs")
        return
    atomic_text(example, content, dry_run)
    if not dry_run:
        remember(manifest, SETTINGS_EXAMPLE_RELATIVE, example, True)


def validate_model_token(value: str, option: str) -> str:
    if not MODEL_TOKEN.fullmatch(value):
        raise InstallError(
            f"invalid model for {option}: use 1-128 letters, digits, dots, underscores, or hyphens"
        )
    return value


def validate_claude_model(value: str, option: str) -> str:
    """Accept only native Anthropic Claude aliases or full Claude model IDs."""
    validate_model_token(value, option)
    if value in {"opus", "sonnet", "haiku", "fable"} or value.startswith("claude-"):
        return value
    raise InstallError(
        f"invalid native model for {option}: Anthropic Claude roles require opus, sonnet, haiku, fable, or a claude-* ID; "
        "use --external-provider openai/deepseek for proposal-only external models"
    )


def validate_effort(value: str, option: str, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise InstallError(f"invalid effort for {option}: choose {', '.join(sorted(allowed))}")
    return value


def parse_override(values: list[str], option: str, *, effort: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise InstallError(f"{option} must use ROLE=VALUE: {value}")
        role, selected = (part.strip() for part in value.split("=", 1))
        if role not in ROLES:
            raise InstallError(f"unknown role for {option}: {role}")
        if effort:
            allowed = CLAUDE_OWNER_EFFORTS if role == "owner" else CLAUDE_EFFORTS
            result[role] = validate_effort(selected, option, allowed) if selected else ""
        else:
            result[role] = validate_claude_model(selected, option)
    return result


def prompt_choice(prompt: str, choices: list[tuple[str, str]], default: str) -> str:
    print(f"\n{prompt}")
    for index, (value, label) in enumerate(choices, 1):
        suffix = " (recommended / onerilen)" if value == default else ""
        print(f"  {index}. {label}{suffix}")
    raw = input(f"Select / Secim [{next(i for i, item in enumerate(choices, 1) if item[0] == default)}]: ").strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(choices):
        return choices[int(raw) - 1][0]
    for value, _ in choices:
        if raw == value:
            return value
    raise InstallError(f"invalid selection / gecersiz secim: {raw}")


def interactive_options(args: argparse.Namespace) -> None:
    print("\n+------------------------------------------------------------------+")
    print("| Ustam - Guided Setup                                            |")
    print("+------------------------------------------------------------------+")
    print("  Native routing : Anthropic Claude only")
    print("  Safety         : one native Claude writer; bounded review loops")
    print("  External APIs  : disabled by default; proposal-only when enabled")
    print("\n[1/3] NATIVE CLAUDE PROFILE")
    args.preset = prompt_choice(
        "Choose how the native Claude team should work / Profil secin:",
        [
            ("balanced", "Balanced / Dengeli - daily quality, speed, and cost"),
            ("quality", "Quality / Yuksek kalite - strongest Claude routing"),
            ("economy", "Economy / Ekonomik - lighter Claude routing"),
            ("custom", "Custom / Ozel - choose each Claude model and effort"),
            ("quota-saver", "Quota saver / Kota tasarrufu - lower-effort bounded routing"),
        ],
        args.preset,
    )
    if args.preset == "custom":
        base = PRESETS["balanced"]
        for role in ROLES:
            default_model, default_effort = base[role]
            label = ROLE_LABELS[role]
            model = input(f"{label} ({role}) Claude modeli [{default_model}]: ").strip() or default_model
            effort = input(f"{label} ({role}) effort / dusunme duzeyi [{default_effort}]: ").strip() or default_effort
            validate_claude_model(model, f"{role} model")
            validate_effort(
                effort,
                f"{role} effort",
                CLAUDE_OWNER_EFFORTS if role == "owner" else CLAUDE_EFFORTS,
            )
            args.role_model.append(f"{role}={model}")
            args.role_effort.append(f"{role}={effort}")
    print("\n[2/3] OPTIONAL EXTERNAL PROPOSAL PROVIDER")
    print("  Warning: reviewed task context is sent to the selected provider API.")
    print("  The provider cannot read/write the workspace; Claude stays sole writer.")
    external = prompt_choice(
        "Select an external API / Harici API secin:",
        [
            ("none", "None / Yok - Claude models only"),
            ("openai", "OpenAI GPT - read-only implementation proposals"),
            ("deepseek", "DeepSeek V4.1 Flash - read-only proposals"),
        ],
        args.external_provider or "none",
    )
    args.external_provider = external
    if external != "none":
        spec = EXTERNAL_PROVIDERS[external]
        default_model = args.external_model or str(spec["default_model"])
        default_effort = args.external_effort or str(spec["default_effort"])
        args.external_model = input(f"{spec['label']} model [{default_model}]: ").strip() or default_model
        args.external_effort = input(f"Reasoning effort / Dusunme duzeyi [{default_effort}]: ").strip() or default_effort
        validate_model_token(args.external_model, "external model")
        validate_effort(args.external_effort, "external effort", spec["efforts"])
        key = str(spec["key"])
        if not os.environ.get(key):
            print(f"  Note: {key} is not set. The key will never be saved to project files.")
    print("\n[3/3] CONFIGURATION REVIEW")
    print(f"  Native profile : {args.preset}")
    print("  Native models  : Anthropic Claude only")
    if external == "none":
        print("  External API   : none")
    else:
        print(f"  External API   : {EXTERNAL_PROVIDERS[external]['label']} (proposal-only)")
        print(f"  External model : {args.external_model}")
        print(f"  External effort: {args.external_effort}")
    print("  API key storage: environment only")
    print("--------------------------------------------------------------------")


def routing_for(args: argparse.Namespace, saved: dict[str, Any] | None = None) -> dict[str, tuple[str, str]]:
    preset = "balanced" if args.preset == "custom" else args.preset
    routing = dict(PRESETS[preset])
    if saved is not None:
        if not isinstance(saved, dict) or set(saved) != set(ROLES):
            raise InstallError("saved routing is incomplete; review it before updating")
        for role in ROLES:
            value = saved[role]
            if not isinstance(value, dict) or not isinstance(value.get("model"), str) or not isinstance(value.get("effort"), str):
                raise InstallError("saved routing is invalid; review it before updating")
            routing[role] = (value["model"], value["effort"])
    models = parse_override(args.role_model, "--role-model")
    efforts = parse_override(args.role_effort, "--role-effort", effort=True)
    for role in ROLES:
        model, effort = routing[role]
        routing[role] = (models.get(role, model), efforts.get(role, effort))
        validate_claude_model(routing[role][0], f"{role} model")
        validate_model_effort(routing[role][0], routing[role][1], f"{role} effort", chief=role == "owner")
    return routing


def check_model_cli_versions(models) -> None:
    selected = set(models) & set(model_capabilities.MINIMUM_VERSIONS)
    if not selected:
        return
    executable = shutil.which('claude')
    if not executable:
        raise InstallError('Claude CLI version cannot be verified for pinned models; install a supported CLI or explicitly choose an alias with default effort.')
    try:
        result = subprocess.run([executable, '--version'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False)
        match = re.search(r'(?<![0-9])(\d+)\.(\d+)\.(\d+)(?![0-9])', (result.stdout + result.stderr)[:4096])
        version = tuple(map(int, match.groups())) if result.returncode == 0 and match else None
        for model in selected:
            model_capabilities.validate_cli_version(model, version)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise InstallError(str(error)) from error


def validate_model_effort(model: str, effort: str, option: str, *, chief: bool = False) -> str:
    try:
        return model_capabilities.validate_selection(model, effort, chief=chief)
    except ValueError as error:
        raise InstallError(f"{option}: {error}") from error


def render_agent(source: Path, model: str, effort: str) -> str:
    validate_model_effort(model, effort, "agent effort")
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    model_index, effort_index = None, None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            break
        if line.startswith("model:"):
            model_index = index
            lines[index] = f"model: {model}\n"
        elif line.startswith("effort:"):
            effort_index = index
            lines[index] = f"effort: {effort}\n" if effort else ""
    if effort and effort_index is None:
        if model_index is None:
            raise InstallError('Agent frontmatter is missing model')
        lines.insert(model_index + 1, f"effort: {effort}\n")
    return "".join(lines)


def settings_content(routing: dict[str, tuple[str, str]]) -> str:
    model, effort = routing["owner"]
    validate_model_effort(model, effort, "owner settings", chief=True)
    return json.dumps(
        {
            "model": model,
            **({"effortLevel": effort} if effort else {}),
            "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"},
        },
        indent=2,
    ) + "\n"


def mcp_server(target: Path, provider: str, model: str, effort: str) -> dict[str, Any]:
    spec = EXTERNAL_PROVIDERS[provider]
    env_prefix = "OPENAI" if provider == "openai" else "DEEPSEEK"
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(target / spec["bridge"])],
        "env": {f"{env_prefix}_MODEL": model, f"{env_prefix}_REASONING_EFFORT": effort},
    }


def write_mcp_example(
    target: Path,
    manifest: dict[str, Any],
    provider: str,
    desired: dict[str, Any],
    dry_run: bool,
    output: list[str],
) -> None:
    spec = EXTERNAL_PROVIDERS[provider]
    relative = Path(spec["example"])
    server_name = str(spec["server"])
    content = json.dumps({"mcpServers": {server_name: desired}}, indent=2) + "\n"
    path = target / relative
    if path.is_symlink():
        output.append(f"SKIP {relative}: destination is a symlink")
        return
    if path.exists() and not same_text(path, content):
        output.append(f"SKIP {relative}: existing example differs")
        return
    output.append(f"INSTALL {relative} (merge manually)")
    atomic_text(path, content, dry_run)
    if not dry_run:
        remember(manifest, relative, path, True)


def manifest_mcp_entry(manifest: dict[str, Any], provider: str) -> dict[str, Any] | None:
    if provider == "openai" and isinstance(manifest.get("mcp_entry"), dict):
        return manifest["mcp_entry"]
    entries = manifest.get("mcp_entries")
    if isinstance(entries, dict) and isinstance(entries.get(provider), dict):
        return entries[provider]
    return None


def set_manifest_mcp_entry(manifest: dict[str, Any], provider: str, entry: dict[str, Any]) -> None:
    manifest.setdefault("mcp_entries", {})[provider] = entry
    if provider == "openai":
        manifest["mcp_entry"] = entry  # v0.3 compatibility


def clear_manifest_mcp_entry(manifest: dict[str, Any], provider: str) -> None:
    entries = manifest.get("mcp_entries")
    if isinstance(entries, dict):
        entries.pop(provider, None)
        if not entries:
            manifest.pop("mcp_entries", None)
    if provider == "openai":
        manifest.pop("mcp_entry", None)


def install_mcp(
    target: Path,
    manifest: dict[str, Any],
    provider: str,
    model: str,
    effort: str,
    dry_run: bool,
    output: list[str],
) -> bool:
    spec = EXTERNAL_PROVIDERS[provider]
    server_name = str(spec["server"])
    desired = mcp_server(target, provider, model, effort)
    path = target / MCP_RELATIVE
    if path.is_symlink():
        output.append(f"PRESERVE {MCP_RELATIVE}: destination is a symlink")
        write_mcp_example(target, manifest, provider, desired, dry_run, output)
        return False
    if not path.exists():
        output.append(f"INSTALL {MCP_RELATIVE}")
        atomic_text(path, json.dumps({"mcpServers": {server_name: desired}}, indent=2) + "\n", dry_run)
        if not dry_run:
            set_manifest_mcp_entry(manifest, provider, {"owned_file": True, "server": desired})
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError("mcpServers must be an object")
    except (OSError, json.JSONDecodeError, ValueError):
        output.append(f"PRESERVE {MCP_RELATIVE}: invalid or unsupported structure")
        write_mcp_example(target, manifest, provider, desired, dry_run, output)
        return False
    existing = servers.get(server_name)
    if existing is not None and existing != desired:
        previous = manifest_mcp_entry(manifest, provider)
        if isinstance(previous, dict) and existing == previous.get("server"):
            output.append(f"UPDATE {MCP_RELATIVE}: change {server_name} model/effort")
            data["mcpServers"][server_name] = desired
            atomic_text(path, json.dumps(data, indent=2) + "\n", dry_run)
            if not dry_run:
                set_manifest_mcp_entry(
                    manifest,
                    provider,
                    {"owned_file": bool(previous.get("owned_file")), "server": desired},
                )
            return True
        output.append(f"PRESERVE {MCP_RELATIVE}: {server_name} already differs")
        write_mcp_example(target, manifest, provider, desired, dry_run, output)
        return False
    if existing == desired:
        output.append(f"UNCHANGED {MCP_RELATIVE} entry")
        return True
    output.append(f"UPDATE {MCP_RELATIVE}: add {server_name}")
    data["mcpServers"][server_name] = desired
    atomic_text(path, json.dumps(data, indent=2) + "\n", dry_run)
    if not dry_run:
        set_manifest_mcp_entry(manifest, provider, {"owned_file": False, "server": desired})
    return True


def disable_mcp(
    target: Path, manifest: dict[str, Any], provider: str, dry_run: bool, output: list[str]
) -> tuple[bool, bool]:
    spec = EXTERNAL_PROVIDERS[provider]
    server_name = str(spec["server"])
    label = str(spec["label"])
    entry = manifest_mcp_entry(manifest, provider)
    path = safe_uninstall_path(target, MCP_RELATIVE)
    if not isinstance(entry, dict):
        if not path.exists():
            return True, False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True, False
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict) and server_name in servers:
            output.append(f"KEEP {MCP_RELATIVE}: {server_name} is not installer-owned")
            return True, True
        return True, False
    if not path.exists():
        if not dry_run:
            clear_manifest_mcp_entry(manifest, provider)
        return True, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        output.append(f"KEEP {MCP_RELATIVE}: modified after installation; {label} remains configured")
        return False, True
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or servers.get(server_name) != entry.get("server"):
        output.append(f"KEEP {MCP_RELATIVE}: {server_name} changed after installation; {label} remains configured")
        return False, True
    output.append(
        f"REMOVE {MCP_RELATIVE}"
        if entry.get("owned_file") and len(servers) == 1 and set(data) == {"mcpServers"}
        else f"UPDATE {MCP_RELATIVE}: remove {server_name}"
    )
    if not dry_run:
        del servers[server_name]
        if entry.get("owned_file") and not servers and set(data) == {"mcpServers"}:
            path.unlink()
        else:
            atomic_text(path, json.dumps(data, indent=2) + "\n", False)
        clear_manifest_mcp_entry(manifest, provider)
    return True, False


def remove_optional_file(target: Path, manifest: dict[str, Any], relative: Path, dry_run: bool, output: list[str]) -> None:
    entry = manifest.get("files", {}).get(relative.as_posix())
    if not isinstance(entry, dict) or not entry.get("owned"):
        return
    path = safe_uninstall_path(target, relative)
    if not path.exists():
        if not dry_run:
            manifest["files"].pop(relative.as_posix(), None)
        return
    if not path.is_file() or digest(path) != entry.get("sha256"):
        output.append(f"KEEP {relative}: modified after installation")
        return
    output.append(f"REMOVE {relative}")
    if not dry_run:
        path.unlink()
        manifest["files"].pop(relative.as_posix(), None)


def disable_external_provider(
    target: Path, manifest: dict[str, Any], provider: str, dry_run: bool, output: list[str]
) -> bool:
    spec = EXTERNAL_PROVIDERS[provider]
    bridge = Path(spec["bridge"])
    example = Path(spec["example"])
    disabled, preserve_bridge = disable_mcp(target, manifest, provider, dry_run, output)
    if not disabled:
        return False
    if preserve_bridge:
        output.append(f"KEEP {bridge}: an unowned MCP entry may still use it")
    else:
        remove_optional_file(target, manifest, bridge, dry_run, output)
    remove_optional_file(target, manifest, example, dry_run, output)
    return True


def disable_external_openai(target: Path, manifest: dict[str, Any], dry_run: bool, output: list[str]) -> bool:
    """Backward-compatible helper retained for v0.3 callers and tests."""
    return disable_external_provider(target, manifest, "openai", dry_run, output)


def uninstall_mcp_provider(
    target: Path, manifest: dict[str, Any], provider: str, dry_run: bool, output: list[str],
    expected_state: dict[str, str | None] | None = None,
    mutation_started: list[bool] | None = None,
) -> Path | None:
    spec = EXTERNAL_PROVIDERS[provider]
    server_name = str(spec["server"])
    entry = manifest_mcp_entry(manifest, provider)
    if not isinstance(entry, dict):
        return None
    if expected_state is not None and expected_state[MCP_RELATIVE.as_posix()] is None:
        return None
    path = safe_uninstall_path(target, MCP_RELATIVE)
    if not path.exists():
        return None
    try:
        original = path.read_bytes()
        if expected_state is not None and hashlib.sha256(original).hexdigest() != expected_state[MCP_RELATIVE.as_posix()]:
            raise InstallError(f"file changed during uninstall: {MCP_RELATIVE}")
        data = json.loads(original.decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        output.append(f"KEEP {MCP_RELATIVE}: modified after installation")
        return Path(spec["bridge"])
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or servers.get(server_name) != entry.get("server"):
        output.append(f"KEEP {MCP_RELATIVE}: modified after installation")
        return Path(spec["bridge"])
    output.append(f"REMOVE {MCP_RELATIVE}" if entry.get("owned_file") and len(servers) == 1 and set(data) == {"mcpServers"} else f"UPDATE {MCP_RELATIVE}: remove {server_name}")
    if dry_run:
        return None
    del servers[server_name]
    replacement = None if entry.get("owned_file") and not servers and set(data) == {"mcpServers"} else json.dumps(data, indent=2) + "\n"
    if mutation_started is not None:
        mutation_started[0] = True
    mutate_verified_uninstall_file(target, MCP_RELATIVE, original, replacement)
    return None


def uninstall_mcp(target: Path, manifest: dict[str, Any], dry_run: bool, output: list[str],
                  expected_state: dict[str, str | None] | None = None,
                  mutation_started: list[bool] | None = None) -> set[str]:
    retained_bridges: set[str] = set()
    for provider in EXTERNAL_PROVIDERS:
        bridge = uninstall_mcp_provider(target, manifest, provider, dry_run, output, expected_state, mutation_started)
        if bridge is not None:
            retained_bridges.add(bridge.as_posix())
    return retained_bridges


def remove_managed_block(text: str) -> tuple[str, bool]:
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start < 0 or end < 0 or end < start:
        return text, False
    end += len(END_MARKER)
    before = text[:start].rstrip()
    after = text[end:].lstrip("\n")
    if before and after:
        result = before + "\n\n" + after
    elif before:
        result = before + "\n"
    else:
        result = after
    return result, True


def managed_block_digest(text: str) -> str | None:
    start = text.find(START_MARKER)
    end = text.find(END_MARKER, start + len(START_MARKER)) if start >= 0 else -1
    if end < 0:
        return None
    block = text[start:end + len(END_MARKER)]
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def install_claude_block(root: Path, target: Path, manifest: dict[str, Any], dry_run: bool, output: list[str]) -> None:
    destination = target / "CLAUDE.md"
    block = (root / "templates/CLAUDE.block.md").read_text(encoding="utf-8").strip()
    roster = manifest.get("roster", [])
    if roster:
        if not isinstance(roster, list) or len(roster) > 99:
            raise InstallError("invalid saved helper team")
        lines = ["\n### Selected helper team",
                 "Use these configured helper names for delegated execution; their count is capacity, not a requirement to spawn all at once. Assign one writer per scope. The main session only coordinates and reads short reports."]
        for index, slot in enumerate(roster, 1):
            slot_id = f"slot-{index:02d}"
            if (not isinstance(slot, dict) or slot.get("id") != slot_id or slot.get("role") not in ROLES[1:]
                or not isinstance(slot.get("label"), str) or not re.fullmatch(r"[\w .()/-]{0,60}", slot["label"])):
                raise InstallError("invalid saved helper team")
            if not isinstance(slot.get("model"), str) or not isinstance(slot.get("effort"), str):
                raise InstallError("invalid saved helper model or effort")
            validate_claude_model(slot["model"], slot_id)
            validate_model_effort(slot["model"], slot["effort"], slot_id)
            label = " — " + json.dumps(slot["label"], ensure_ascii=False) if slot["label"] else ""
            lines.append(f"- `orchestra-{slot_id}`: {slot['role']}{label}; model `{slot['model']}`, effort `{slot['effort'] or 'default'}`")
        block = block.replace(END_MARKER, "\n".join(lines) + "\n" + END_MARKER)
    existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
    cleaned, had_block = remove_managed_block(existing)
    content = (cleaned.rstrip() + "\n\n" + block + "\n").lstrip("\n")
    if existing == content:
        output.append("UNCHANGED CLAUDE.md block")
    else:
        output.append("UPDATE CLAUDE.md block" if had_block else "INSTALL CLAUDE.md block")
        atomic_text(destination, content, dry_run)
    if not dry_run:
        manifest["claude_block"] = True
        manifest["claude_block_sha256"] = managed_block_digest(content)


def save_manifest(target: Path, manifest: dict[str, Any], root: Path, dry_run: bool) -> None:
    if dry_run:
        return
    path = target / MANIFEST_RELATIVE
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise InstallError(f"unsafe install manifest: {path}")
    manifest.update({"schema": SCHEMA_VERSION, "tool_version": version(root), "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()})
    atomic_text(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n", False)


def safe_uninstall_path(target: Path, relative: Path) -> Path:
    """Return a contained non-symlink path or reject the uninstall."""
    if relative.is_absolute() or ".." in relative.parts:
        raise InstallError(f"unsafe uninstall path: {relative}")
    current = target
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise InstallError(f"refusing uninstall through symlinked directory: {current}")
    candidate = target / relative
    if candidate.is_symlink():
        raise InstallError(f"refusing to uninstall symlink: {candidate}")
    try:
        candidate.resolve(strict=False).relative_to(target)
    except ValueError as exc:
        raise InstallError(f"uninstall path escapes target: {relative}") from exc
    return candidate


def require_secure_uninstall_backend() -> None:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if not SECURE_UNINSTALL_DIR_FD or any(not isinstance(getattr(os, name, None), int) for name in required):
        raise InstallError("secure uninstall is unsupported on this platform")


def open_uninstall_recovery(root_fd: int, flags: int) -> int:
    """Open the ignored private recovery directory without following symlinks."""
    claude_fd = os.open(".claude", flags, dir_fd=root_fd)
    try:
        runtime_fd = os.open(".bounded-orchestrator", flags, dir_fd=claude_fd)
        try:
            try:
                os.mkdir("backups", 0o700, dir_fd=runtime_fd)
            except FileExistsError:
                pass
            return os.open("backups", flags, dir_fd=runtime_fd)
        finally:
            os.close(runtime_fd)
    finally:
        os.close(claude_fd)


def retain_uninstall_inode(root_fd: int, parent_fd: int, staged: str,
                           relative: Path, flags: int, kind: str) -> None:
    recovery_fd = open_uninstall_recovery(root_fd, flags)
    try:
        name = f"{kind}-{relative.as_posix().replace('/', '_')}-{secrets.token_hex(12)}"
        os.link(staged, name, src_dir_fd=parent_fd,
                dst_dir_fd=recovery_fd, follow_symlinks=False)
    finally:
        os.close(recovery_fd)


def move_failed_stage_to_recovery(root_fd: int, parent_fd: int, staged: str,
                                  relative: Path, flags: int) -> None:
    """Atomically move whichever inode now occupies the failed stage."""
    recovery_fd = open_uninstall_recovery(root_fd, flags)
    try:
        name = f"uninstall-failed-stage-{relative.as_posix().replace('/', '_')}-{secrets.token_hex(12)}"
        os.rename(staged, name, src_dir_fd=parent_fd, dst_dir_fd=recovery_fd)
    finally:
        os.close(recovery_fd)


def recover_failed_replacement(root_fd: int, parent_fd: int, leaf: str,
                               relative: Path, flags: int,
                               created_identity: tuple[int, int]) -> None:
    """Retain a failed replacement or restore an intervening user collision."""
    failed_stage = f".{leaf.lstrip('.')}.bounded-failed-{secrets.token_hex(12)}"
    try:
        os.rename(leaf, failed_stage, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except FileNotFoundError:
        return
    current = os.stat(failed_stage, dir_fd=parent_fd, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != created_identity:
        # The new name belongs to someone else. Restore it without overwriting.
        try:
            os.link(failed_stage, leaf, src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd, follow_symlinks=False)
        except FileExistsError:
            pass
        move_failed_stage_to_recovery(root_fd, parent_fd, failed_stage, relative, flags)
        return
    retain_uninstall_inode(root_fd, parent_fd, failed_stage, relative, flags,
                           "uninstall-failed-replacement")
    # Rename, rather than unlink: an atomic save may replace the stage after
    # the hardlink and must remain recoverable as its own inode.
    move_failed_stage_to_recovery(root_fd, parent_fd, failed_stage, relative, flags)


def verify_uninstall_parents(descriptors: list[int], relative: Path, flags: int) -> None:
    check_fd = descriptors[0]
    for index, part in enumerate(relative.parts[:-1], 1):
        reopened = os.open(part, flags, dir_fd=check_fd)
        try:
            current_dir = os.fstat(reopened)
            original_dir = os.fstat(descriptors[index])
            if (current_dir.st_dev, current_dir.st_ino) != (original_dir.st_dev, original_dir.st_ino):
                raise InstallError(f"directory changed during uninstall: {relative}")
        finally:
            os.close(reopened)
        check_fd = descriptors[index]


def mutate_verified_uninstall_file(
    target: Path, relative: Path, expected: bytes, replacement: str | None = None,
) -> None:
    """Change a verified file; retain its inode in private recovery."""
    require_secure_uninstall_backend()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = [os.open(target, flags)]
    staged: str | None = None
    try:
        for part in relative.parts[:-1]:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        parent_fd = descriptors[-1]
        leaf = relative.name
        file_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            initial = os.fstat(file_fd)
            if not stat.S_ISREG(initial.st_mode):
                raise InstallError(f"file changed during uninstall: {relative}")
            os.lseek(file_fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(file_fd), "rb") as handle:
                actual = handle.read()
            read_stat = os.fstat(file_fd)
            if actual != expected or (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (
                read_stat.st_dev, read_stat.st_ino, read_stat.st_size, read_stat.st_mtime_ns, read_stat.st_ctime_ns
            ):
                raise InstallError(f"file changed during uninstall: {relative}")

            # Ensure the still-visible project path names the same directories.
            verify_uninstall_parents(descriptors, relative, flags)
            linked = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            latest = os.fstat(file_fd)
            if (linked.st_dev, linked.st_ino, linked.st_size, linked.st_mtime_ns, linked.st_ctime_ns) != (
                initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns
            ) or (latest.st_size, latest.st_mtime_ns, latest.st_ctime_ns) != (
                initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns
            ):
                raise InstallError(f"file changed during uninstall: {relative}")

            staged = f".{leaf}.bounded-remove-{secrets.token_hex(12)}"
            os.rename(leaf, staged, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            staged_stat = os.stat(staged, dir_fd=parent_fd, follow_symlinks=False)
            os.lseek(file_fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(file_fd), "rb") as handle:
                staged_content = handle.read()
            if (staged_stat.st_dev, staged_stat.st_ino) != (initial.st_dev, initial.st_ino) or staged_content != expected:
                raise InstallError(f"file changed during uninstall: {relative}")
            verify_uninstall_parents(descriptors, relative, flags)
            retain_uninstall_inode(descriptors[0], parent_fd, staged, relative, flags, "uninstall")
            verify_uninstall_parents(descriptors, relative, flags)
            if replacement is not None:
                created_fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                created_stat = os.fstat(created_fd)
                try:
                    with os.fdopen(created_fd, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(replacement)
                        handle.flush()
                        os.fsync(handle.fileno())
                except BaseException:
                    recover_failed_replacement(descriptors[0], parent_fd, leaf, relative, flags,
                                               (created_stat.st_dev, created_stat.st_ino))
                    raise
            os.unlink(staged, dir_fd=parent_fd)
            staged = None
            verify_uninstall_parents(descriptors, relative, flags)
        finally:
            os.close(file_fd)
    except BaseException:
        if staged is not None:
            try:
                os.link(staged, leaf, src_dir_fd=descriptors[-1], dst_dir_fd=descriptors[-1], follow_symlinks=False)
                os.unlink(staged, dir_fd=descriptors[-1])
            except FileExistsError:
                # Keep the staged original if another file appeared at its name.
                pass
        raise
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def uninstall(target: Path, manifest: dict[str, Any], dry_run: bool, output: list[str],
              expected_state: dict[str, str | None] | None = None,
              approved_actions: list[str] | None = None,
              mutation_started: list[bool] | None = None) -> None:
    require_secure_uninstall_backend()
    entries = manifest.get("files", {})
    safe_paths: dict[str, Path] = {}
    for name, entry in entries.items():
        if not isinstance(name, str) or (name not in ALLOWED_UNINSTALL_FILES and not ROSTER_AGENT_PATH.fullmatch(name)):
            raise InstallError(f"manifest contains unmanaged uninstall path: {name!r}")
        if not isinstance(entry, dict):
            raise InstallError(f"manifest contains invalid entry for: {name}")
        safe_paths[name] = safe_uninstall_path(target, Path(name))
    manifest_path = safe_uninstall_path(target, MANIFEST_RELATIVE)
    claude = safe_uninstall_path(target, Path("CLAUDE.md"))
    manifest_bytes = manifest_path.read_bytes() if manifest_path.exists() else None

    if expected_state is not None:
        for name, expected in expected_state.items():
            path = safe_uninstall_path(target, Path(name))
            current = digest(path) if path.is_file() else None
            if current != expected:
                raise InstallError(f"file changed during uninstall: {name}")
    if approved_actions is not None:
        planned: list[str] = []
        uninstall(target, manifest, True, planned)
        if planned != approved_actions:
            raise InstallError("uninstall actions changed since preview")

    retained_bridges = uninstall_mcp(target, manifest, dry_run, output, expected_state, mutation_started)

    for name, entry in sorted(entries.items(), reverse=True):
        path = safe_paths[name]
        if expected_state is not None and expected_state[name] is None:
            continue
        if not entry.get("owned") or not path.exists():
            continue
        if name in retained_bridges:
            output.append(f"KEEP {name}: retained MCP entry may still use it")
            continue
        if Path(name) == RUNTIME_IGNORE_RELATIVE:
            output.append(f"KEEP {name}: protects retained private runtime data")
            continue
        if not path.is_file():
            output.append(f"KEEP {name}: modified after installation")
            continue
        original = path.read_bytes()
        if expected_state is not None and hashlib.sha256(original).hexdigest() != expected_state[name]:
            raise InstallError(f"file changed during uninstall: {name}")
        if hashlib.sha256(original).hexdigest() != entry.get("sha256"):
            output.append(f"KEEP {name}: modified after installation")
            continue
        output.append(f"REMOVE {name}")
        if not dry_run:
            if mutation_started is not None:
                mutation_started[0] = True
            mutate_verified_uninstall_file(target, Path(name), original)
    if manifest.get("claude_block") and (expected_state is None or expected_state["CLAUDE.md"] is not None) and claude.is_file():
        current = claude.read_text(encoding="utf-8")
        if expected_state is not None and hashlib.sha256(current.encode("utf-8")).hexdigest() != expected_state["CLAUDE.md"]:
            raise InstallError("file changed during uninstall: CLAUDE.md")
        cleaned, removed = remove_managed_block(current)
        if removed:
            if not manifest.get("claude_block_sha256"):
                output.append("KEEP CLAUDE.md block: original content not recorded; review manually")
            elif managed_block_digest(current) != manifest["claude_block_sha256"]:
                output.append("KEEP CLAUDE.md block: modified after installation")
            else:
                output.append("REMOVE CLAUDE.md block")
                if not dry_run:
                    if mutation_started is not None:
                        mutation_started[0] = True
                    mutate_verified_uninstall_file(target, Path("CLAUDE.md"), current.encode("utf-8"), cleaned or None)
    output.append(f"REMOVE {MANIFEST_RELATIVE}")
    if approved_actions is not None and output != approved_actions:
        raise InstallError("uninstall actions changed since preview")
    if not dry_run and manifest_path.exists():
        if manifest_bytes is None:
            raise InstallError("install manifest changed during uninstall")
        if expected_state is not None and hashlib.sha256(manifest_bytes).hexdigest() != expected_state[MANIFEST_RELATIVE.as_posix()]:
            raise InstallError("install manifest changed during uninstall")
        if mutation_started is not None:
            mutation_started[0] = True
        mutate_verified_uninstall_file(target, MANIFEST_RELATIVE, manifest_bytes)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="existing project directory")
    parser.add_argument("--dry-run", action="store_true", help="preview without writing")
    parser.add_argument("--force", action="store_true", help="replace conflicting managed files after backup")
    parser.add_argument("--force-settings", action="store_true", help="replace .claude/settings.json after backup")
    parser.add_argument("--uninstall", action="store_true", help="remove unchanged files installed by this tool")
    parser.add_argument("--interactive", action="store_true", help="show Turkish profile and provider choices")
    parser.add_argument(
        "--preset",
        choices=("balanced", "quality", "economy", "custom", "quota-saver"),
        default=None,
        help="prepared model/effort profile (new install: balanced; update: keep saved choices)",
    )
    parser.add_argument("--role-model", action="append", default=[], metavar="ROLE=MODEL", help="override one role model; repeatable")
    parser.add_argument("--role-effort", action="append", default=[], metavar="ROLE=EFFORT", help="override one role effort; repeatable")
    parser.add_argument(
        "--external-provider",
        choices=("none", "openai", "deepseek"),
        default=None,
        help="proposal-only external API provider; omitted preserves an existing provider",
    )
    openai = parser.add_mutually_exclusive_group()
    openai.add_argument("--external-openai", dest="external_openai", action="store_true", help="compatibility alias for --external-provider openai")
    openai.add_argument("--no-external-openai", dest="external_openai", action="store_false", help="remove an unchanged installer-owned OpenAI proposal role")
    deepseek = parser.add_mutually_exclusive_group()
    deepseek.add_argument("--external-deepseek", dest="external_deepseek", action="store_true", help="compatibility alias for --external-provider deepseek")
    deepseek.add_argument("--no-external-deepseek", dest="external_deepseek", action="store_false", help="remove an unchanged installer-owned DeepSeek proposal role")
    parser.set_defaults(external_openai=None, external_deepseek=None)
    parser.add_argument("--external-model", default=None, help="external provider model ID")
    parser.add_argument("--external-effort", default=None, help="external provider reasoning effort")
    return parser.parse_args(argv)


def normalize_external_options(args: argparse.Namespace) -> tuple[str | None, set[str]]:
    requested = args.external_provider
    explicit_disable: set[str] = set()
    aliases = (("openai", args.external_openai), ("deepseek", args.external_deepseek))
    for provider, state in aliases:
        if state is True:
            if requested not in (None, provider):
                raise InstallError("choose only one external proposal provider")
            requested = provider
        elif state is False:
            explicit_disable.add(provider)
    if requested in explicit_disable:
        raise InstallError(f"cannot enable and disable {requested} together")
    if requested is not None and requested != "none":
        spec = EXTERNAL_PROVIDERS[requested]
        args.external_model = args.external_model or str(spec["default_model"])
        args.external_effort = args.external_effort or str(spec["default_effort"])
    elif args.external_model is not None or args.external_effort is not None:
        raise InstallError("--external-model/--external-effort require an external provider")
    return requested, explicit_disable


def print_install_summary(
    target: Path,
    args: argparse.Namespace,
    provider: str | None,
    output: list[str],
) -> None:
    if not args.interactive:
        print("\n".join(output))
        return
    print("\n+------------------------------------------------------------------+")
    print("| INSTALL RESULT                                                   |")
    print("+------------------------------------------------------------------+")
    print(f"  Status          : {'preview complete' if args.dry_run else 'installation complete'}")
    print(f"  Target          : {target}")
    print(f"  Native profile  : {args.preset} (Anthropic Claude only)")
    print(f"  External API    : {provider or 'none'}")
    print("\n  File actions")
    if output:
        for line in output:
            print(f"    - {line}")
    else:
        print("    - no changes")
    print("\n  Next steps")
    print("    1. Restart Claude Code in the target project.")
    if provider:
        key = EXTERNAL_PROVIDERS[provider]["key"]
        print(f"    2. Set {key} in the shell that launches Claude Code.")
        print("    3. Share only reviewed, necessary context with the proposal tool.")
    else:
        print("    2. Give Claude a normal task; native routing is ready.")
    print("--------------------------------------------------------------------")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(argv)
    explicit_profile = args.preset is not None or args.interactive
    args.preset = args.preset or "balanced"
    root = source_root()
    try:
        if args.interactive and not args.uninstall:
            interactive_options(args)
        requested_provider, explicit_disable = normalize_external_options(args)
        target = args.target.expanduser().resolve()
        if not target.is_dir():
            raise InstallError(f"target must be an existing directory: {target}")
        if target == root:
            raise InstallError("target must differ from the installer repository")
        ensure_sources(root)
        manifest = load_manifest(target)
        output: list[str] = []
        if args.uninstall:
            if not (target / MANIFEST_RELATIVE).exists():
                raise InstallError("no installation manifest found")
            uninstall(target, manifest, args.dry_run, output)
        else:
            saved_routing = manifest.get("routing") if not explicit_profile else None
            if saved_routing is not None:
                args.preset = manifest.get("preset", "custom")
                if args.preset not in PRESETS and args.preset != "custom":
                    raise InstallError("saved profile is invalid; review it before updating")
            routing = routing_for(args, saved_routing)
            check_model_cli_versions(pair[0] for pair in routing.values())
            if saved_routing is not None and (args.role_model or args.role_effort):
                args.preset = "custom"
            if requested_provider not in (None, "none"):
                spec = EXTERNAL_PROVIDERS[requested_provider]
                assert args.external_model is not None and args.external_effort is not None
                validate_model_token(args.external_model, "--external-model")
                validate_effort(args.external_effort, "--external-effort", spec["efforts"])
            for relative in BASE_MANAGED_FILES:
                content = work_protocol_wrapper(target) if relative == Path('.claude/tools/work_protocol') else None
                if relative.parent == Path(".claude/agents"):
                    model, effort = routing[relative.stem]
                    content = render_agent(root / relative, model, effort)
                install_one(root, target, relative, manifest, args.force, args.dry_run, output, content)
            install_settings(
                root,
                target,
                manifest,
                args.force_settings,
                args.dry_run,
                output,
                settings_content(routing),
            )
            effective = {
                "openai": bool(manifest.get("external_openai", False)),
                "deepseek": bool(manifest.get("external_deepseek", False)),
            }
            providers_to_disable = set(explicit_disable)
            if requested_provider == "none":
                providers_to_disable.update(EXTERNAL_PROVIDERS)
            elif requested_provider in EXTERNAL_PROVIDERS:
                providers_to_disable.update(set(EXTERNAL_PROVIDERS) - {requested_provider})
            for provider in sorted(providers_to_disable):
                disabled = disable_external_provider(
                    target, manifest, provider, args.dry_run, output
                )
                effective[provider] = not disabled
                if (
                    not disabled
                    and requested_provider in EXTERNAL_PROVIDERS
                    and provider != requested_provider
                ):
                    raise InstallError(
                        f"cannot safely switch to {requested_provider}: the existing {provider} MCP entry was modified"
                    )
            if requested_provider in EXTERNAL_PROVIDERS:
                spec = EXTERNAL_PROVIDERS[requested_provider]
                bridge = Path(spec["bridge"])
                install_one(root, target, bridge, manifest, args.force, args.dry_run, output)
                effective[requested_provider] = install_mcp(
                    target,
                    manifest,
                    requested_provider,
                    args.external_model,
                    args.external_effort,
                    args.dry_run,
                    output,
                )
            install_claude_block(root, target, manifest, args.dry_run, output)
            if not args.dry_run:
                manifest["preset"] = args.preset
                manifest["routing"] = {role: {"model": model, "effort": effort} for role, (model, effort) in routing.items()}
                manifest["external_openai"] = effective["openai"]
                manifest["external_deepseek"] = effective["deepseek"]
                enabled = [provider for provider, state in effective.items() if state]
                manifest["external_provider"] = enabled[0] if len(enabled) == 1 else None
            save_manifest(target, manifest, root, args.dry_run)
        active_provider = None
        if not args.uninstall:
            if not args.dry_run and manifest.get("external_provider"):
                active_provider = manifest.get("external_provider")
            elif requested_provider in EXTERNAL_PROVIDERS:
                active_provider = requested_provider
            elif requested_provider is None:
                active_provider = manifest.get("external_provider")
        print_install_summary(target, args, active_provider, output)
        return 0
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
