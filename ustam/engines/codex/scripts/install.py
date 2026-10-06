#!/usr/bin/env python3
"""Safely install Ustam into an existing repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import shlex
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
START_MARKER = "<!-- codex-bounded-orchestrator:start -->"
END_MARKER = "<!-- codex-bounded-orchestrator:end -->"
MANIFEST_RELATIVE = Path(".codex/.bounded-orchestrator/install.json")
BACKUP_RELATIVE = Path(".codex/.bounded-orchestrator/backups")
SAFE_UNINSTALL_SUPPORTED = (all(hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'pread')) and
                            all(operation in os.supports_dir_fd for operation in (os.open, os.stat, os.rename,
                                                                                   os.unlink, os.mkdir, os.link)))
CONFIG_EXAMPLE_RELATIVE = Path(".codex/bounded-orchestrator.config.example.toml")
CONFIG_RELATIVE = Path(".codex/config.toml")
ANTHROPIC_BRIDGE_RELATIVE = Path(".codex/tools/anthropic_mcp.py")
DEEPSEEK_BRIDGE_RELATIVE = Path(".codex/tools/deepseek_mcp.py")
EXTERNAL_BRIDGES = {
    "anthropic": ANTHROPIC_BRIDGE_RELATIVE,
    "deepseek": DEEPSEEK_BRIDGE_RELATIVE,
}

ROLE_FILES = {
    "fast_lookup": Path(".codex/agents/fast-lookup.toml"),
    "explorer": Path(".codex/agents/explorer.toml"),
    "researcher": Path(".codex/agents/researcher.toml"),
    "acceptance_test_author": Path(".codex/agents/acceptance-test-author.toml"),
    "implementer": Path(".codex/agents/implementer.toml"),
    "verifier": Path(".codex/agents/verifier.toml"),
    "failure_analyst": Path(".codex/agents/failure-analyst.toml"),
    "qa_operator": Path(".codex/agents/qa-operator.toml"),
    "reviewer": Path(".codex/agents/reviewer.toml"),
    "advisor": Path(".codex/agents/advisor.toml"),
}
TEAM_SLOT_FILES = {
    f"team_slot_{index:02d}": Path(f".codex/agents/team-slot-{index:02d}.toml")
    for index in range(1, 51)
}
ALL_ROLES = ("owner", *ROLE_FILES)
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
EXTERNAL_EFFORTS = {
    "anthropic": ("auto", "low", "medium", "high", "xhigh", "max"),
    "deepseek": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
}
EXTERNAL_DEFAULTS = {
    "anthropic": ("claude-sonnet-5-5", "high"),
    "deepseek": ("deepseek-flash", "high"),
}
NATIVE_MODEL_PATTERN = re.compile(r"^gpt-[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
EXTERNAL_MODEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/-]*$", re.IGNORECASE)

BALANCED_PROFILE = {
    "owner": ("gpt-6-astra", "medium"),
    "fast_lookup": ("gpt-6-luna", "medium"),
    "explorer": ("gpt-6-luna", "high"),
    "researcher": ("gpt-6.1-sol", "medium"),
    "acceptance_test_author": ("gpt-6.1-sol", "medium"),
    "implementer": ("gpt-6.1-sol", "high"),
    "verifier": ("gpt-6.1-sol", "high"),
    "failure_analyst": ("gpt-6.1-sol", "high"),
    "qa_operator": ("gpt-6.1-sol", "medium"),
    "reviewer": ("gpt-6-astra", "medium"),
    "advisor": ("gpt-6-astra", "xhigh"),
}
PRESETS = {
    "balanced": BALANCED_PROFILE,
    "quality": {
        **BALANCED_PROFILE,
        "owner": ("gpt-6-astra", "high"),
        "explorer": ("gpt-6-astra", "high"),
        "researcher": ("gpt-6-astra", "high"),
        "implementer": ("gpt-6-astra", "high"),
        "verifier": ("gpt-6-astra", "high"),
        "failure_analyst": ("gpt-6-astra", "high"),
        "qa_operator": ("gpt-6-astra", "high"),
        "reviewer": ("gpt-6-astra", "xhigh"),
        "advisor": ("gpt-6-astra", "max"),
    },
    "economy": {
        **BALANCED_PROFILE,
        "owner": ("gpt-6.1-sol", "medium"),
        "fast_lookup": ("gpt-6-luna", "low"),
        "explorer": ("gpt-6-luna", "medium"),
        "researcher": ("gpt-6-luna", "high"),
        "implementer": ("gpt-6.1-sol", "medium"),
        "verifier": ("gpt-6-luna", "high"),
        "failure_analyst": ("gpt-6.1-sol", "medium"),
        "qa_operator": ("gpt-6-luna", "high"),
        "reviewer": ("gpt-6.1-sol", "high"),
        "advisor": ("gpt-6.1-sol", "high"),
    },
    "quota-saver": {
        **BALANCED_PROFILE,
        "owner": ("gpt-6-astra", "low"),
        "fast_lookup": ("gpt-6-luna", "low"),
        "explorer": ("gpt-6-luna", "low"),
        "researcher": ("gpt-6.1-sol", "low"),
        "implementer": ("gpt-6.1-sol", "medium"),
        "verifier": ("gpt-6-luna", "medium"),
        "failure_analyst": ("gpt-6.1-sol", "medium"),
        "qa_operator": ("gpt-6-luna", "medium"),
        "reviewer": ("gpt-6-astra", "low"),
        "advisor": ("gpt-6-astra", "low"),
    },
    "custom": BALANCED_PROFILE,
}

# Compact context routing, opt-in; preserve existing presets and custom choices.
PRESETS["focused"] = {
    role: ("gpt-6-luna", "high") if role in ("fast_lookup", "explorer")
    else ("gpt-6.1-sol", "medium")
    for role in ALL_ROLES
}

MANAGED_RELATIVE_FILES = (
    Path(".codex/tools/candidate.py"),
    Path(".codex/tools/ledger.py"),
    Path(".codex/tools/usage_report.py"),
    Path(".codex/tools/local_eval.py"),
    Path(".codex/bounded-orchestrator.eval.example.json"),
    Path(".codex/.candidate/.gitignore"),
    Path(".codex/.bounded-orchestrator/.gitignore"),
    Path(".agents/skills/bounded-orchestrator/SKILL.md"),
    Path(".agents/skills/bounded-orchestrator/references/task-contract.md"),
    Path(".agents/skills/bounded-orchestrator/references/review-protocol.md"),
    Path(".agents/skills/bounded-orchestrator/references/escalation.md"),
    Path(".agents/skills/bounded-orchestrator-ui-design/SKILL.md"),
    Path(".agents/skills/bounded-orchestrator-security-review/SKILL.md"),
    Path(".codex/tools/work_protocol.py"),
    Path(".codex/tools/work_protocol_core.py"),
    Path(".codex/tools/work_protocol"),
)

ALLOWED_MANIFEST_FILES = frozenset(
    (*MANAGED_RELATIVE_FILES, *ROLE_FILES.values(), *TEAM_SLOT_FILES.values(), CONFIG_RELATIVE,
     CONFIG_EXAMPLE_RELATIVE, *EXTERNAL_BRIDGES.values())
)


def work_protocol_wrapper(target):
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--work-protocol', 'codex']
    else:
        command = [sys.executable, str((target / '.codex/tools/work_protocol.py').resolve())]
    command += ['--project', str(target.resolve())]
    return '#!/bin/sh\n# Fixed project and installer runtime; no paid call.\nexec ' + ' '.join(shlex.quote(a) for a in command) + ' "$@"\n'


class InstallError(RuntimeError):
    """Expected installer failure."""


def configure_console_output() -> None:
    """Keep the active encoding but replace characters it cannot represent."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            # Captured/custom streams may not be reconfigurable. They retain their
            # existing behavior; normal TextIOWrapper consoles use the safe mode.
            pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def tool_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise InstallError("VERSION is missing from the installer repository.") from exc


def sha256_path(path: Path) -> str:
    if path.is_symlink():
        return hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_file(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    if left.is_dir() or right.is_dir():
        return False
    return sha256_path(left) == sha256_path(right)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def same_text(path: Path, text: str) -> bool:
    return path.is_file() and not path.is_symlink() and sha256_path(path) == sha256_text(text)


def require_expected_bytes(path: Path, expected: bytes | None, relative: Path) -> None:
    if expected is None:
        return
    if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
        raise InstallError(f"Managed file changed during installation: {relative}")


def load_manifest(target: Path) -> dict[str, Any]:
    path = target / MANIFEST_RELATIVE
    refuse_symlink_destination(target, MANIFEST_RELATIVE)
    if path.exists() and not path.is_file():
        raise InstallError(f"Install manifest is not a regular file: {path}")
    if not path.exists():
        return {
            "schema": SCHEMA_VERSION,
            "files": {},
            "agents_block": False,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot read install manifest: {path}") from exc
    if data.get("schema") != SCHEMA_VERSION or not isinstance(data.get("files"), dict):
        raise InstallError(f"Unsupported install manifest: {path}")
    return data


def atomic_write_text(path: Path, text: str, dry_run: bool, *, exclusive: bool = False) -> None:
    if dry_run:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_copy(source: Path, destination: Path, dry_run: bool, *, exclusive: bool = False) -> None:
    if dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        if exclusive:
            os.link(temporary, destination)
        else:
            os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_file(target_root: Path, path: Path, dry_run: bool) -> Path:
    try:
        relative = path.relative_to(target_root)
    except ValueError as exc:
        raise InstallError(f"Refusing to back up a path outside the target: {path}") from exc

    backup = target_root / BACKUP_RELATIVE / timestamp_for_path() / relative
    suffix = 1
    original = backup
    while backup.exists():
        if backup.is_symlink():
            raise InstallError(f"Refusing symlink backup path: {backup}")
        backup = original.with_name(f"{original.name}.{suffix}")
        suffix += 1
    if backup.is_symlink():
        raise InstallError(f"Refusing symlink backup path: {backup}")

    for parent in (backup.parent, *backup.parent.parents):
        if parent == target_root:
            break
        if parent.is_symlink():
            raise InstallError(f"Refusing symlink backup directory: {parent}")

    if not dry_run:
        backup.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            backup.symlink_to(os.readlink(path))
        else:
            # Exclusive creation refuses even a dangling symlink planted between
            # preflight and the write; private config backups start mode 0600.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(backup, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as destination, path.open("rb") as source:
                    shutil.copyfileobj(source, destination)
            except Exception:
                backup.unlink(missing_ok=True)
                raise
    return backup


def validate_target(target: Path, root: Path) -> Path:
    target = target.expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise InstallError(f"Target must be an existing directory: {target}")
    if target == root:
        raise InstallError("Target repository must be different from the installer repository.")
    return target


def ensure_source_files(root: Path) -> None:
    required = [root / path for path in (*MANAGED_RELATIVE_FILES, *ROLE_FILES.values())]
    required.extend(
        [
            root / CONFIG_RELATIVE,
            root / "presets/sol-owner.config.toml",
            *[root / path for path in EXTERNAL_BRIDGES.values()],
            root / "templates/AGENTS.block.md",
            root / "VERSION",
        ]
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise InstallError("Installer source is incomplete:\n  - " + "\n  - ".join(missing))


def parse_override(value: str, option: str) -> tuple[str, str]:
    if "=" not in value:
        raise InstallError(f"{option} must use ROLE=VALUE")
    role, selected = (part.strip() for part in value.split("=", 1))
    if role not in ALL_ROLES:
        raise InstallError(f"Unknown role {role!r} in {option}")
    if not selected:
        raise InstallError(f"Empty value for {role!r} in {option}")
    return role, selected


def validate_native_model(model: str, option: str = "native role model") -> str:
    """Keep native Codex roles on OpenAI GPT models.

    Prefix validation deliberately preserves forward compatibility for future
    gpt-* IDs without pretending the installer can verify account access.
    """
    if not isinstance(model, str) or not NATIVE_MODEL_PATTERN.fullmatch(model):
        raise InstallError(
            f"{option} must be an OpenAI GPT model ID beginning with 'gpt-'. "
            "Use --external-provider anthropic or deepseek for other brands."
        )
    return model


def validate_external_selection(
    provider: str, model: str | None, effort: str | None
) -> tuple[str, str]:
    if provider == "none":
        return "", ""
    if provider not in EXTERNAL_DEFAULTS:
        raise InstallError(f"Unsupported external provider: {provider!r}")
    default_model, default_effort = EXTERNAL_DEFAULTS[provider]
    selected_model = model or default_model
    selected_effort = effort or default_effort
    expected_prefix = "claude-" if provider == "anthropic" else "deepseek-"
    if (
        not EXTERNAL_MODEL_PATTERN.fullmatch(selected_model)
        or not selected_model.lower().startswith(expected_prefix)
        or len(selected_model) > 120
    ):
        raise InstallError(
            f"{provider} external model must be a {expected_prefix} model ID "
            "up to 120 characters."
        )
    allowed_efforts = EXTERNAL_EFFORTS[provider]
    if selected_effort not in allowed_efforts:
        raise InstallError(
            f"Unsupported {provider} effort {selected_effort!r}; choose one of: "
            + ", ".join(allowed_efforts)
        )
    if provider == "anthropic" and (selected_model == "claude-haiku-4-5-20251001") != (selected_effort == "auto"):
        raise InstallError("Claude Haiku 4.5 requires auto effort; other Claude models require an explicit supported effort")
    return selected_model, selected_effort


def resolve_profile(
    preset: str,
    legacy_profile: str | None,
    model_overrides: Iterable[str],
    effort_overrides: Iterable[str],
) -> dict[str, tuple[str, str]]:
    selected = {role: tuple(values) for role, values in PRESETS[preset].items()}
    if legacy_profile == "sol":
        selected["owner"] = ("gpt-6.1-sol", "high")
    elif legacy_profile == "astra":
        selected["owner"] = ("gpt-6-astra", "medium")
    for raw in model_overrides:
        role, model = parse_override(raw, "--role-model")
        validate_native_model(model, f"--role-model {role}")
        selected[role] = (model, selected[role][1])
    for raw in effort_overrides:
        role, effort = parse_override(raw, "--role-effort")
        if effort not in EFFORTS:
            raise InstallError(
                f"Unsupported effort {effort!r}; choose one of: {', '.join(EFFORTS)}"
            )
        selected[role] = (selected[role][0], effort)
    return selected


def replace_toml_value(text: str, key: str, value: str) -> str:
    pattern = rf"(?m)^{re.escape(key)}\s*=\s*\"[^\"]*\"\s*$"
    updated, count = re.subn(pattern, f"{key} = {json.dumps(value)}", text, count=1)
    if count != 1:
        raise InstallError(f"Template is missing {key}")
    return updated


def render_role_config(root: Path, role: str, model: str, effort: str) -> str:
    text = (root / ROLE_FILES[role]).read_text(encoding="utf-8")
    text = replace_toml_value(text, "model", model)
    return replace_toml_value(text, "model_reasoning_effort", effort)


def render_root_config(
    root: Path,
    target: Path,
    settings: dict[str, tuple[str, str]],
    external_provider: str,
    external_model: str,
    external_effort: str,
) -> str:
    text = (root / CONFIG_RELATIVE).read_text(encoding="utf-8")
    text = replace_toml_value(text, "model", settings["owner"][0])
    text = replace_toml_value(text, "model_reasoning_effort", settings["owner"][1])
    text = replace_toml_value(text, "review_model", settings["reviewer"][0])
    text = replace_toml_value(text, "default_subagent_model", settings["explorer"][0])
    text = replace_toml_value(
        text, "default_subagent_reasoning_effort", settings["explorer"][1]
    )
    if external_provider == "anthropic":
        command = json.dumps(sys.executable)
        bridge = json.dumps(str((target / ANTHROPIC_BRIDGE_RELATIVE).resolve()))
        text += (
            "\n# Optional read-only Anthropic bridge. The API key is inherited from "
            "ANTHROPIC_API_KEY and is never stored here.\n"
            "[mcp_servers.anthropic_claude]\n"
            f"command = {command}\n"
            f"args = [{bridge}, \"--model\", {json.dumps(external_model)}, "
            f"\"--effort\", {json.dumps(external_effort)}]\n"
            'env_vars = ["ANTHROPIC_API_KEY"]\n'
            "enabled = true\n"
            'enabled_tools = ["claude_implementation_proposal"]\n'
            "startup_timeout_sec = 10\n"
            "tool_timeout_sec = 180\n"
        )
    elif external_provider == "deepseek":
        command = json.dumps(sys.executable)
        bridge = json.dumps(str((target / DEEPSEEK_BRIDGE_RELATIVE).resolve()))
        text += (
            "\n# Optional read-only DeepSeek bridge. The API key is inherited from "
            "DEEPSEEK_API_KEY and is never stored here.\n"
            "[mcp_servers.deepseek_proposals]\n"
            f"command = {command}\n"
            f"args = [{bridge}, \"--model\", {json.dumps(external_model)}, "
            f"\"--effort\", {json.dumps(external_effort)}]\n"
            'env_vars = ["DEEPSEEK_API_KEY"]\n'
            "enabled = true\n"
            'enabled_tools = ["deepseek_implementation_proposal"]\n'
            "startup_timeout_sec = 10\n"
            "tool_timeout_sec = 180\n"
        )
    return text


def previous_owned(manifest: dict[str, Any], relative: Path) -> bool:
    entry = manifest.get("files", {}).get(relative.as_posix(), {})
    return bool(entry.get("owned", False))


def unchanged_owned(manifest: dict[str, Any], relative: Path, destination: Path) -> bool:
    entry = manifest.get("files", {}).get(relative.as_posix(), {})
    return bool(
        entry.get("owned", False)
        and isinstance(entry.get("sha256"), str)
        and (destination.exists() or destination.is_symlink())
        and not destination.is_dir()
        and sha256_path(destination) == entry["sha256"]
    )


def remember_file(
    manifest: dict[str, Any], relative: Path, destination: Path, owned: bool
) -> None:
    manifest.setdefault("files", {})[relative.as_posix()] = {
        "sha256": sha256_path(destination),
        "owned": owned,
    }


def install_file(
    *,
    root: Path,
    target: Path,
    relative: Path,
    manifest: dict[str, Any],
    force: bool,
    dry_run: bool,
    messages: list[str],
) -> None:
    source = root / relative
    destination = target / relative

    if destination.exists() and destination.is_dir():
        messages.append(f"SKIP {relative}: destination is a directory")
        return

    if not destination.exists() and not destination.is_symlink():
        messages.append(f"INSTALL {relative}")
        atomic_copy(source, destination, dry_run, exclusive=True)
        if not dry_run:
            remember_file(manifest, relative, destination, True)
        return

    if same_file(source, destination):
        owned = previous_owned(manifest, relative)
        messages.append(f"UNCHANGED {relative}")
        if not dry_run:
            remember_file(manifest, relative, destination, owned)
        return

    if not force:
        messages.append(f"SKIP {relative}: existing file differs; use --force to replace")
        return

    backup = backup_file(target, destination, dry_run)
    messages.append(f"BACKUP {relative} -> {backup.relative_to(target)}")
    messages.append(f"REPLACE {relative}")
    atomic_copy(source, destination, dry_run)
    if not dry_run:
        remember_file(manifest, relative, destination, True)


def install_text_file(
    *,
    target: Path,
    relative: Path,
    text: str,
    manifest: dict[str, Any],
    force: bool,
    dry_run: bool,
    messages: list[str],
    create_only: bool = False,
    expected_bytes: bytes | None = None,
) -> None:
    destination = target / relative
    require_expected_bytes(destination, expected_bytes, relative)
    if create_only and (destination.exists() or destination.is_symlink()):
        raise InstallError(f"New managed file appeared during installation: {relative}")
    if destination.exists() and destination.is_dir():
        messages.append(f"SKIP {relative}: destination is a directory")
        return
    if not destination.exists() and not destination.is_symlink():
        messages.append(f"INSTALL {relative}")
        atomic_write_text(destination, text, dry_run, exclusive=True)
        if not dry_run:
            remember_file(manifest, relative, destination, True)
        return
    if same_text(destination, text):
        owned = previous_owned(manifest, relative)
        messages.append(f"UNCHANGED {relative}")
        if not dry_run:
            remember_file(manifest, relative, destination, owned)
        return
    if not force:
        messages.append(f"SKIP {relative}: existing file differs; use --force to replace")
        return
    backup = backup_file(target, destination, dry_run)
    require_expected_bytes(destination, expected_bytes, relative)
    messages.append(f"BACKUP {relative} -> {backup.relative_to(target)}")
    messages.append(f"REPLACE {relative}")
    atomic_write_text(destination, text, dry_run)
    if not dry_run:
        remember_file(manifest, relative, destination, True)


def install_config(
    *,
    target: Path,
    config_text: str,
    preset: str,
    manifest: dict[str, Any],
    force_config: bool,
    dry_run: bool,
    messages: list[str],
    create_only: bool = False,
    expected_bytes: bytes | None = None,
) -> bool:
    """Install desired config and report whether it becomes the active config."""
    relative = CONFIG_RELATIVE
    destination = target / relative
    require_expected_bytes(destination, expected_bytes, relative)
    if create_only and (destination.exists() or destination.is_symlink()):
        raise InstallError(f"New configuration appeared during installation: {relative}")

    if destination.exists() and destination.is_dir():
        messages.append(f"SKIP {relative}: destination is a directory")
        return False

    if not destination.exists() and not destination.is_symlink():
        messages.append(f"INSTALL {relative} ({preset} preset)")
        atomic_write_text(destination, config_text, dry_run, exclusive=True)
        if not dry_run:
            remember_file(manifest, relative, destination, True)
        return True

    if same_text(destination, config_text):
        owned = previous_owned(manifest, relative)
        messages.append(f"UNCHANGED {relative}")
        if not dry_run:
            remember_file(manifest, relative, destination, owned)
        return True

    if unchanged_owned(manifest, relative, destination):
        messages.append(f"UPDATE {relative} ({preset} preset; installer-owned)")
        require_expected_bytes(destination, expected_bytes, relative)
        atomic_write_text(destination, config_text, dry_run)
        if not dry_run:
            remember_file(manifest, relative, destination, True)
        return True

    if force_config:
        backup = backup_file(target, destination, dry_run)
        require_expected_bytes(destination, expected_bytes, relative)
        messages.append(f"BACKUP {relative} -> {backup.relative_to(target)}")
        messages.append(f"REPLACE {relative} ({preset} preset)")
        atomic_write_text(destination, config_text, dry_run)
        if not dry_run:
            remember_file(manifest, relative, destination, True)
        return True

    example = target / CONFIG_EXAMPLE_RELATIVE
    if example.exists() and example.is_dir():
        messages.append(
            f"SKIP {CONFIG_EXAMPLE_RELATIVE}: destination is a directory"
        )
        return False

    example_was_absent = not example.exists() and not example.is_symlink()
    if example_was_absent or same_text(example, config_text):
        action = "WRITE" if example_was_absent else "UNCHANGED"
        messages.append(
            f"PRESERVE {relative}; {action} {CONFIG_EXAMPLE_RELATIVE} for manual merge"
        )
        if example_was_absent:
            atomic_write_text(example, config_text, dry_run, exclusive=True)
        if not dry_run:
            owned = True if example_was_absent else previous_owned(
                manifest, CONFIG_EXAMPLE_RELATIVE
            )
            remember_file(manifest, CONFIG_EXAMPLE_RELATIVE, example, owned)
        return False

    text = example.read_text(encoding="utf-8", errors="replace")
    if "managed-by: codex-bounded-orchestrator" in text:
        backup = backup_file(target, example, dry_run)
        messages.append(
            f"BACKUP {CONFIG_EXAMPLE_RELATIVE} -> {backup.relative_to(target)}"
        )
        messages.append(
            f"PRESERVE {relative}; REFRESH {CONFIG_EXAMPLE_RELATIVE} for manual merge"
        )
        atomic_write_text(example, config_text, dry_run)
        if not dry_run:
            remember_file(manifest, CONFIG_EXAMPLE_RELATIVE, example, True)
        return False

    messages.append(
        f"PRESERVE {relative}; SKIP {CONFIG_EXAMPLE_RELATIVE} because it also differs"
    )
    return False


def active_external_providers(target: Path) -> set[str] | None:
    """Return external providers referenced by active config, or None if unknown."""
    path = target / CONFIG_RELATIVE
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return set()

    providers: set[str] = set()
    for server_name, server in servers.items():
        if not isinstance(server, dict):
            continue
        arguments = server.get("args", [])
        if not isinstance(arguments, list):
            arguments = []
        searchable = [server_name, *(item for item in arguments if isinstance(item, str))]
        for provider, relative in EXTERNAL_BRIDGES.items():
            if any(
                relative.name in value
                or (provider == "anthropic" and value == "anthropic_claude")
                or (provider == "deepseek" and value == "deepseek_proposals")
                for value in searchable
            ):
                providers.add(provider)
    return providers


def normalize_block(raw: str) -> str:
    text = raw.strip()
    if not text.startswith(START_MARKER) or not text.endswith(END_MARKER):
        raise InstallError("templates/AGENTS.block.md is missing managed markers.")
    return text


def merge_agents_text(existing: str, block: str) -> str:
    start = existing.find(START_MARKER)
    end = existing.find(END_MARKER)

    if (start == -1) != (end == -1):
        raise InstallError("AGENTS.md contains only one bounded-orchestrator marker.")

    if start != -1:
        if end < start:
            raise InstallError("AGENTS.md managed markers are in the wrong order.")
        end += len(END_MARKER)
        merged = existing[:start].rstrip() + "\n\n" + block + existing[end:]
        return merged.strip() + "\n"

    if not existing.strip():
        return block + "\n"
    return existing.rstrip() + "\n\n" + block + "\n"


def install_agents_block(
    *,
    root: Path,
    target: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    messages: list[str],
) -> None:
    path = target / "AGENTS.md"
    if path.exists() and path.is_dir():
        messages.append("SKIP AGENTS.md: destination is a directory")
        return
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    block = normalize_block((root / "templates/AGENTS.block.md").read_text(encoding="utf-8"))
    merged = merge_agents_text(existing, block)
    if existing == merged:
        messages.append("UNCHANGED AGENTS.md managed block")
    else:
        action = "UPDATE" if path.exists() else "CREATE"
        messages.append(f"{action} AGENTS.md managed block")
        atomic_write_text(path, merged, dry_run)
    if not dry_run:
        manifest["agents_block"] = True


def write_manifest(
    *,
    root: Path,
    target: Path,
    preset: str,
    settings: dict[str, tuple[str, str]],
    external_provider: str,
    external_model: str,
    external_effort: str,
    manifest: dict[str, Any],
    dry_run: bool,
    create_only: bool = False,
    expected_bytes: bytes | None = None,
) -> None:
    manifest.update(
        {
            "schema": SCHEMA_VERSION,
            "tool_version": tool_version(root),
            "profile": "sol" if settings["owner"] == ("gpt-6.1-sol", "high") else "astra",
            "preset": preset,
            "role_settings": {
                role: {"model": model, "effort": effort}
                for role, (model, effort) in settings.items()
            },
            "external_provider": external_provider,
            "external_model": external_model if external_provider != "none" else None,
            "external_effort": external_effort if external_provider != "none" else None,
            "installed_utc": utc_now(),
        }
    )
    if dry_run:
        return
    path = target / MANIFEST_RELATIVE
    refuse_symlink_destination(target, MANIFEST_RELATIVE)
    if path.exists() and not path.is_file():
        raise InstallError(f"Install manifest is not a regular file: {path}")
    require_expected_bytes(path, expected_bytes, MANIFEST_RELATIVE)
    atomic_write_text(
        path,
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        False,
        exclusive=create_only or not path.exists(),
    )


def remove_managed_block(target: Path, dry_run: bool, messages: list[str], expected_bytes: bytes | None = None,
                         recovery_run: str | None = None) -> None:
    path = target / "AGENTS.md"
    if expected_bytes is not None:
        require_uninstall_snapshot(path, expected_bytes, Path('AGENTS.md'))
    if not path.exists() or path.is_dir():
        return
    current_bytes = path.read_bytes()
    existing = current_bytes.decode("utf-8")
    start = existing.find(START_MARKER)
    end = existing.find(END_MARKER)
    if start == -1 and end == -1:
        return
    if start == -1 or end == -1 or end < start:
        messages.append("KEEP AGENTS.md: malformed managed markers")
        return
    end += len(END_MARKER)
    updated = (existing[:start].rstrip() + "\n\n" + existing[end:].lstrip()).strip()
    if updated:
        updated += "\n"
        messages.append("REMOVE AGENTS.md managed block")
        if not dry_run:
            require_uninstall_snapshot(path, current_bytes, Path('AGENTS.md'))
            unlink_uninstall_file(target, Path('AGENTS.md'), current_bytes,
                                  replacement_bytes=updated.encode('utf-8'), recovery_run=recovery_run)
    else:
        messages.append("REMOVE empty AGENTS.md")
        if not dry_run:
            require_uninstall_snapshot(path, current_bytes, Path('AGENTS.md'))
            unlink_uninstall_file(target, Path('AGENTS.md'), current_bytes, recovery_run=recovery_run)


def prune_empty_directories(target: Path, relative_files: Iterable[Path]) -> None:
    candidates: set[Path] = set()
    for relative in relative_files:
        parent = (target / relative).parent
        while parent != target and target in parent.parents:
            candidates.add(parent)
            parent = parent.parent
    for directory in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def safe_manifest_relative(value: str) -> Path | None:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative not in ALLOWED_MANIFEST_FILES:
        return None
    return relative


def refuse_symlink_destination(target: Path, relative: Path) -> None:
    path = target / relative
    for candidate in (path, *path.parents):
        if candidate == target:
            break
        if candidate.is_symlink():
            raise InstallError(f"Refusing symlink uninstall path: {relative}")


def require_uninstall_snapshot(path: Path, expected: bytes | None, relative: Path) -> None:
    refuse_symlink_destination(path.parents[len(relative.parts) - 1], relative)
    if (path.read_bytes() if path.is_file() else None) != expected:
        raise InstallError(f"Project file changed during uninstall: {relative}")


def read_uninstall_fd(fd: int) -> bytes:
    chunks = []
    offset = 0
    while chunk := os.pread(fd, 1024 * 1024, offset):
        chunks.append(chunk)
        offset += len(chunk)
    return b''.join(chunks)


def write_uninstall_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)


def link_uninstall_recovery(root_fd: int, parent_fd: int, staged_name: str,
                            relative: Path, recovery_run: str) -> None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.dup(root_fd)
    try:
        for part in ('.codex', '.bounded-orchestrator', 'backups', recovery_run):
            if part in ('backups', recovery_run):
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        backup_name = str(relative).replace('/', '__')
        os.link(staged_name, backup_name, src_dir_fd=parent_fd, dst_dir_fd=fd,
                follow_symlinks=False)
    finally:
        os.close(fd)


def unlink_uninstall_file(target: Path, relative: Path, expected_bytes: bytes | None,
                          expected_digest: str | None = None,
                          replacement_bytes: bytes | None = None,
                          recovery_run: str | None = None) -> None:
    """Unlink through no-follow directory handles after checking the exact file."""
    if recovery_run is None:
        recovery_run = 'uninstall-' + secrets.token_hex(8)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = [os.open(target, directory_flags)]
    try:
        for part in relative.parts[:-1]:
            descriptors.append(os.open(part, directory_flags, dir_fd=descriptors[-1]))
        parent_fd = descriptors[-1]
        leaf = relative.name
        file_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            initial = os.fstat(file_fd)
            if not stat.S_ISREG(initial.st_mode):
                raise InstallError(f"Project file changed during uninstall: {relative}")
            current = read_uninstall_fd(file_fd)
            after_read = os.fstat(file_fd)
            if (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (
                    after_read.st_dev, after_read.st_ino, after_read.st_size, after_read.st_mtime_ns, after_read.st_ctime_ns):
                raise InstallError(f"Project file changed during uninstall: {relative}")
            if (expected_bytes is not None and current != expected_bytes) or (
                    expected_digest is not None and hashlib.sha256(current).hexdigest() != expected_digest):
                raise InstallError(f"Project file changed during uninstall: {relative}")
            # Reopen the parent path from the project root. A renamed parent or
            # a substituted symlink must not redirect the pending unlink.
            check_fd = descriptors[0]
            for index, part in enumerate(relative.parts[:-1], 1):
                reopened = os.open(part, directory_flags, dir_fd=check_fd)
                try:
                    current_dir = os.fstat(reopened)
                    original_dir = os.fstat(descriptors[index])
                    if (current_dir.st_dev, current_dir.st_ino) != (original_dir.st_dev, original_dir.st_ino):
                        raise InstallError(f"Project file changed during uninstall: {relative}")
                finally:
                    os.close(reopened)
                check_fd = descriptors[index]
            linked = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            latest = os.fstat(file_fd)
            if (linked.st_dev, linked.st_ino, linked.st_size, linked.st_mtime_ns, linked.st_ctime_ns) != (
                    initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) or (
                    latest.st_size, latest.st_mtime_ns, latest.st_ctime_ns) != (
                    initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns):
                raise InstallError(f"Project file changed during uninstall: {relative}")
            quarantine = f'.{leaf}.bounded-uninstall-{secrets.token_hex(16)}'
            os.rename(leaf, quarantine, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            replacement_created = False
            try:
                staged = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
                latest = os.fstat(file_fd)
                if (staged.st_dev, staged.st_ino) != (initial.st_dev, initial.st_ino) or (
                        latest.st_size, latest.st_mtime_ns, latest.st_ctime_ns) != (
                        staged.st_size, staged.st_mtime_ns, staged.st_ctime_ns) or (
                        staged.st_size, staged.st_mtime_ns) != (initial.st_size, initial.st_mtime_ns) or (
                        read_uninstall_fd(file_fd) != current):
                    raise InstallError(f"Project file changed during uninstall: {relative}")
                link_uninstall_recovery(descriptors[0], parent_fd, quarantine, relative, recovery_run)
                if replacement_bytes is not None:
                    replacement_fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                             stat.S_IMODE(initial.st_mode), dir_fd=parent_fd)
                    replacement_created = True
                    try:
                        link_uninstall_recovery(descriptors[0], parent_fd, leaf,
                                                relative.with_name(relative.name + '.replacement'), recovery_run)
                        write_uninstall_fd(replacement_fd, replacement_bytes)
                    finally:
                        os.close(replacement_fd)
                    if read_uninstall_fd(file_fd) != current:
                        raise InstallError(f"Project file changed during uninstall: {relative}")
                os.unlink(quarantine, dir_fd=parent_fd)
                latest = os.fstat(file_fd)
                # Unlink updates ctime, but a concurrent content write must
                # still prevent a successful uninstall response.
                if (latest.st_size, latest.st_mtime_ns) != (initial.st_size, initial.st_mtime_ns) or (
                        read_uninstall_fd(file_fd) != current):
                    raise InstallError(f"Project file changed during uninstall: {relative}")
            except Exception as exc:
                if replacement_created:
                    raise InstallError(f"Uninstall rollback incomplete; {relative} left in place and original retained in backups/{recovery_run}") from exc
                try:
                    os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    try:
                        os.link(quarantine, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                                follow_symlinks=False)
                    except FileExistsError as restore_exc:
                        raise InstallError(f"Uninstall rollback incomplete; original retained in backups/{recovery_run}") from restore_exc
                    except FileNotFoundError:
                        restored_fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                              stat.S_IMODE(initial.st_mode), dir_fd=parent_fd)
                        try:
                            write_uninstall_fd(restored_fd, read_uninstall_fd(file_fd))
                        finally:
                            os.close(restored_fd)
                    else:
                        os.unlink(quarantine, dir_fd=parent_fd)
                else:
                    raise InstallError(f"Uninstall rollback incomplete; original retained in backups/{recovery_run}") from exc
                raise
        finally:
            os.close(file_fd)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def uninstall(target: Path, dry_run: bool, expected_files: Mapping[str, bytes | None] | None = None) -> int:
    if not dry_run and not SAFE_UNINSTALL_SUPPORTED:
        raise InstallError('Safe uninstall requires POSIX no-follow directory operations on this platform')
    messages: list[str] = []
    recovery_run = 'uninstall-' + secrets.token_hex(8)
    manifest_path = target / MANIFEST_RELATIVE
    refuse_symlink_destination(target, MANIFEST_RELATIVE)
    manifest_bytes = manifest_path.read_bytes() if manifest_path.is_file() else None
    manifest = load_manifest(target)
    files = manifest.get("files", {})

    # Validate the complete owned surface before the first removal. A changed
    # parent directory must never redirect one of the later unlinks outside.
    for relative in [Path("AGENTS.md"), MANIFEST_RELATIVE, *(safe_manifest_relative(item) for item in files)]:
        if relative is not None:
            refuse_symlink_destination(target, relative)

    # Bind the apply step to the complete reviewed snapshot before changing
    # any file, including manifest-listed paths absent during the preview.
    if expected_files is not None and not dry_run:
        for relative in [Path('AGENTS.md'), MANIFEST_RELATIVE, *(safe_manifest_relative(item) for item in files)]:
            if relative is not None:
                require_uninstall_snapshot(target / relative, expected_files.get(str(relative)), relative)

    runtime_root = target / MANIFEST_RELATIVE.parent
    runtime_ignore = MANIFEST_RELATIVE.parent / '.gitignore'
    # Backups and console snapshots may contain previous private settings.
    # Keep their local ignore rule after the manifest is removed.
    keep_runtime_ignore = runtime_root.is_dir() and any(
        child.name not in {MANIFEST_RELATIVE.name, runtime_ignore.name}
        for child in runtime_root.iterdir()
    )

    for relative_text, entry in sorted(files.items(), reverse=True):
        relative = safe_manifest_relative(relative_text)
        if relative is None:
            messages.append(f"KEEP invalid manifest path: {relative_text}")
            continue
        refuse_symlink_destination(target, relative)
        if expected_files is not None:
            require_uninstall_snapshot(target / relative, expected_files.get(str(relative)), relative)
        if not isinstance(entry, dict):
            messages.append(f"KEEP {relative}: invalid manifest entry")
            continue
        destination = target / relative
        if not entry.get("owned", False):
            messages.append(f"KEEP {relative}: pre-existing file was not owned")
            continue
        if not destination.exists() and not destination.is_symlink():
            continue
        if destination.is_dir():
            messages.append(f"KEEP {relative}: path became a directory")
            continue
        expected = entry.get("sha256")
        actual = sha256_path(destination)
        if expected != actual:
            messages.append(f"KEEP {relative}: modified after installation")
            continue
        if relative == runtime_ignore and (keep_runtime_ignore or (target / BACKUP_RELATIVE).exists()):
            messages.append(f"KEEP {relative}: local backups or runtime data remain")
            continue
        messages.append(f"REMOVE {relative}")
        if not dry_run:
            if expected_files is not None:
                require_uninstall_snapshot(destination, expected_files.get(str(relative)), relative)
            if sha256_path(destination) != expected:
                raise InstallError(f"Project file changed during uninstall: {relative}")
            unlink_uninstall_file(target, relative, expected_files.get(str(relative)) if expected_files is not None else None,
                                  expected, recovery_run=recovery_run)

    refuse_symlink_destination(target, Path('AGENTS.md'))
    if expected_files is not None:
        require_uninstall_snapshot(target / 'AGENTS.md', expected_files.get('AGENTS.md'), Path('AGENTS.md'))
    remove_managed_block(target, dry_run, messages,
                         expected_files.get('AGENTS.md') if expected_files is not None else None,
                         recovery_run=recovery_run)

    if manifest_path.exists():
        if expected_files is not None:
            require_uninstall_snapshot(manifest_path, expected_files.get(str(MANIFEST_RELATIVE)), MANIFEST_RELATIVE)
        messages.append(f"REMOVE {MANIFEST_RELATIVE}")
        if not dry_run:
            require_uninstall_snapshot(manifest_path, manifest_bytes, MANIFEST_RELATIVE)
            unlink_uninstall_file(target, MANIFEST_RELATIVE, manifest_bytes, recovery_run=recovery_run)

    if not dry_run:
        prune_empty_directories(
            target,
            [relative for item in files if (relative := safe_manifest_relative(item))],
        )

    print("\n".join(messages) if messages else "Nothing managed was found.")
    return 0


def install(
    *,
    target: Path,
    preset: str,
    settings: dict[str, tuple[str, str]],
    external_provider: str,
    external_model: str,
    external_effort: str,
    force: bool,
    force_config: bool,
    dry_run: bool,
) -> int:
    root = source_root()
    ensure_source_files(root)
    target = validate_target(target, root)
    manifest = load_manifest(target)
    messages: list[str] = []
    config_text = render_root_config(
        root, target, settings, external_provider, external_model, external_effort
    )

    runtime_ignore = Path(".codex/.bounded-orchestrator/.gitignore")
    runtime_destination = target / runtime_ignore
    runtime_source = root / runtime_ignore
    if (
        (runtime_destination.exists() or runtime_destination.is_symlink())
        and not same_file(runtime_source, runtime_destination)
    ):
        raise InstallError(
            "Reserved runtime path conflicts with this installer: "
            f"{runtime_destination}. Move or reconcile it before installation."
        )

    # Install the runtime ignore before any operation can create backups or a
    # manifest, so interrupted installs do not expose local state to Git.
    install_file(
        root=root,
        target=target,
        relative=runtime_ignore,
        manifest=manifest,
        force=False,
        dry_run=dry_run,
        messages=messages,
    )

    config_applied = install_config(
        target=target,
        config_text=config_text,
        preset=preset,
        manifest=manifest,
        force_config=force_config,
        dry_run=dry_run,
        messages=messages,
    )
    active_providers = (
        ({external_provider} if external_provider != "none" else set())
        if config_applied
        else active_external_providers(target)
    )

    for role, relative in ROLE_FILES.items():
        model, effort = settings[role]
        install_text_file(
            target=target,
            relative=relative,
            text=render_role_config(root, role, model, effort),
            manifest=manifest,
            force=force,
            dry_run=dry_run,
            messages=messages,
        )

    for relative in MANAGED_RELATIVE_FILES:
        if relative == Path('.codex/tools/work_protocol'):
            install_text_file(target=target, relative=relative, text=work_protocol_wrapper(target), manifest=manifest,
                              force=force, dry_run=dry_run, messages=messages)
            continue
        if relative == runtime_ignore:
            continue
        install_file(
            root=root,
            target=target,
            relative=relative,
            manifest=manifest,
            force=force,
            dry_run=dry_run,
            messages=messages,
        )

    if external_provider in EXTERNAL_BRIDGES:
        selected_bridge = EXTERNAL_BRIDGES[external_provider]
        install_file(
            root=root,
            target=target,
            relative=selected_bridge,
            manifest=manifest,
            force=force,
            dry_run=dry_run,
            messages=messages,
        )
        key_name = (
            "ANTHROPIC_API_KEY"
            if external_provider == "anthropic"
            else "DEEPSEEK_API_KEY"
        )
        if key_name not in os.environ:
            messages.append(
                f"NOTE {key_name} is not set; export it before using the external proposal tool"
            )

    for provider, relative in EXTERNAL_BRIDGES.items():
        if provider == external_provider:
            continue
        bridge = target / relative
        if unchanged_owned(manifest, relative, bridge):
            if not config_applied and (
                active_providers is None or provider in active_providers
            ):
                reason = (
                    "active preserved config could not be inspected safely"
                    if active_providers is None
                    else "active preserved config still references it"
                )
                messages.append(f"KEEP {relative}: {reason}")
                continue
            messages.append(f"REMOVE {relative} (provider not selected)")
            if not dry_run:
                bridge.unlink()
                manifest.get("files", {}).pop(relative.as_posix(), None)

    install_agents_block(
        root=root,
        target=target,
        manifest=manifest,
        dry_run=dry_run,
        messages=messages,
    )
    write_manifest(
        root=root,
        target=target,
        preset=preset,
        settings=settings,
        external_provider=external_provider,
        external_model=external_model,
        external_effort=external_effort,
        manifest=manifest,
        dry_run=dry_run,
    )

    heading = "DRY RUN" if dry_run else "INSTALL COMPLETE"
    print_section(heading)
    for message in messages:
        print(f"  - {message}")
    print("\nConfiguration / Yapilandirma")
    print(f"  Target            : {target}")
    print(f"  Native models     : OpenAI GPT only")
    print(f"  Profile           : {preset}")
    print(f"  Requested external: {external_provider}")
    if external_provider != "none":
        print(f"  Requested model   : {external_model} ({external_effort})")
    if config_applied:
        active_label = external_provider
    elif active_providers is None:
        active_label = "unknown (existing config preserved)"
    elif active_providers:
        active_label = ", ".join(sorted(active_providers))
    else:
        active_label = "none detected (existing config preserved)"
    print(f"  Active external   : {active_label}")
    if not config_applied and external_provider != "none":
        print(f"  Manual merge      : {CONFIG_EXAMPLE_RELATIVE}")
    if not dry_run:
        print("\nNext steps / Sonraki adimlar")
        print("  1. Restart Codex / Codex'i yeniden baslatin.")
        if external_provider != "none":
            key_name = (
                "ANTHROPIC_API_KEY"
                if external_provider == "anthropic"
                else "DEEPSEEK_API_KEY"
            )
            print(f"  2. Set {key_name} only in the environment that starts Codex.")
    return 0


def prompt_choice(prompt: str, choices: dict[str, str], default: str) -> str:
    while True:
        answer = input(prompt).strip() or default
        if answer in choices:
            return choices[answer]
        print("Geçersiz seçim / Invalid choice.")


def print_section(title: str) -> None:
    line = "=" * 60
    print(f"\n{line}\n{title}\n{line}")


def interactive_options(
    preset: str | None,
    model_overrides: list[str],
    effort_overrides: list[str],
    external_provider: str | None,
    external_model: str | None,
    external_effort: str | None,
) -> tuple[str, list[str], list[str], str, str, str]:
    print_section("1 / 3  NATIVE PROFILE / YEREL PROFIL")
    print("All native roles use OpenAI GPT models only.")
    print("Tum yerel roller yalniz OpenAI GPT modellerini kullanir.\n")
    print("  1) Balanced / Dengeli       Daily work; recommended")
    print("  2) Quality / Yuksek kalite  Hard or high-impact work")
    print("  3) Economy / Ekonomik       Small, lower-cost work")
    print("  4) Custom / Ozel            Choose GPT model + effort per role")
    print("  5) Quota saver / Kota tasarrufu  Lower-effort bounded routing")
    if preset is None:
        preset = prompt_choice("Seçim / Select [1]: ", {
            "1": "balanced", "2": "quality", "3": "economy", "4": "custom", "5": "quota-saver"
        }, "1")
    if preset == "custom":
        defaults = resolve_profile("balanced", None, model_overrides, effort_overrides)
        print("\nChoose an OpenAI gpt-* model and effort per role.")
        print(
            "Her rol icin OpenAI gpt-* modeli ve efor secin. "
            "Bos birakmak varsayilani korur."
        )
        for role in ALL_ROLES:
            model, effort = defaults[role]
            while True:
                selected_model = input(f"  {role} model [{model}]: ").strip() or model
                try:
                    validate_native_model(selected_model, f"{role} model")
                    break
                except InstallError as exc:
                    print(f"  {exc}")
            while True:
                selected_effort = input(f"  {role} effort [{effort}]: ").strip() or effort
                if selected_effort in EFFORTS:
                    break
                print("  Geçersiz efor / Invalid effort: " + ", ".join(EFFORTS))
            model_overrides.append(f"{role}={selected_model}")
            effort_overrides.append(f"{role}={selected_effort}")

    print_section("2 / 3  EXTERNAL API / HARICI API")
    print("Default is none. External models are read-only proposal tools.")
    print("They cannot access the workspace or replace the native GPT writer.")
    print("API use may send supplied context to the provider and may be billed.\n")
    print("  1) None / Yok (default)")
    print("  2) Anthropic Claude proposal")
    print("  3) DeepSeek proposal")
    if external_provider is None:
        external_provider = prompt_choice(
            "Select / Secim [1]: ",
            {"1": "none", "2": "anthropic", "3": "deepseek"},
            "1",
        )
    if external_provider == "anthropic":
        print("\nClaude modeli / Claude model:")
        print("  1) claude-sonnet-5-5 (dengeli / balanced)")
        print("  2) claude-opus-5-5 (ayrıntılı / detailed)")
        print("  3) claude-fable-5-1")
        print("  4) claude-haiku-4-5-20251001 (ayrı efor ayarı yok / no effort setting)")
        print("  5) Özel model kimliği / Custom model ID")
        choice = prompt_choice("Seçim / Select [1]: ", {"1": "sonnet", "2": "opus", "3": "fable", "4": "haiku", "5": "custom"}, "1")
        prepared = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "fable": "claude-fable-5-1", "haiku": "claude-haiku-4-5-20251001"}
        if choice in prepared:
            external_model = prepared[choice]
        else:
            default_model = external_model or EXTERNAL_DEFAULTS["anthropic"][0]
            external_model = input(f"Model ID [{default_model}]: ").strip() or default_model
        if external_model == "claude-haiku-4-5-20251001":
            external_effort = "auto"
        else:
            external_effort = external_effort or EXTERNAL_DEFAULTS["anthropic"][1]
            while True:
                value = input(f"Claude effort [{external_effort}]: ").strip() or external_effort
                if value in {"low", "medium", "high", "xhigh", "max"}:
                    external_effort = value
                    break
                print("Geçersiz efor / Invalid effort: low, medium, high, xhigh, max")
    elif external_provider == "deepseek":
        default_model, default_effort = EXTERNAL_DEFAULTS["deepseek"]
        print("\nDeepSeek model:")
        print("  1) deepseek-flash (V4.1 Flash; current prepared choice)")
        print("  2) Custom DeepSeek model ID")
        choice = prompt_choice(
            "Select / Secim [1]: ", {"1": "flash", "2": "custom"}, "1"
        )
        if choice == "flash":
            external_model = default_model
        else:
            initial = external_model or default_model
            external_model = input(f"Model ID [{initial}]: ").strip() or initial
        external_effort = external_effort or default_effort
        while True:
            value = (
                input(f"DeepSeek effort [{external_effort}]: ").strip()
                or external_effort
            )
            if value in EXTERNAL_EFFORTS["deepseek"]:
                external_effort = value
                break
            print("Invalid effort: " + ", ".join(EXTERNAL_EFFORTS["deepseek"]))

    external_model, external_effort = validate_external_selection(
        external_provider, external_model, external_effort
    )
    settings = resolve_profile(preset, None, model_overrides, effort_overrides)
    print_section("3 / 3  REVIEW / SON KONTROL")
    print(f"  Native brand      : OpenAI GPT")
    print(f"  Profile           : {preset}")
    print(f"  Owner             : {settings['owner'][0]} ({settings['owner'][1]})")
    print(f"  Implementer       : {settings['implementer'][0]} ({settings['implementer'][1]})")
    print(f"  Verifier          : {settings['verifier'][0]} ({settings['verifier'][1]})")
    print(f"  Reviewer          : {settings['reviewer'][0]} ({settings['reviewer'][1]})")
    print(f"  External proposal : {external_provider}")
    if external_provider != "none":
        print(f"  External model    : {external_model} ({external_effort})")
        print("  Boundary          : proposal only; no workspace access")
    print("\nStarting safe installer / Guvenli kurulum baslatiliyor...")
    return (
        preset, model_overrides, effort_overrides, external_provider,
        external_model, external_effort,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the bounded Codex multi-agent architecture into a repository."
    )
    parser.add_argument("target", type=Path, help="Existing target repository directory.")
    parser.add_argument(
        "--profile",
        choices=("astra", "sol"),
        default=None,
        help="Legacy root-owner switch kept for compatibility (astra or sol).",
    )
    parser.add_argument(
        "--preset",
        choices=tuple(PRESETS),
        default=None,
        help="Prepared routing preset. Non-interactive default: balanced.",
    )
    parser.add_argument(
        "--role-model",
        action="append",
        default=[],
        metavar="ROLE=MODEL",
        help="Override a role model; repeat for multiple roles.",
    )
    parser.add_argument(
        "--role-effort",
        action="append",
        default=[],
        metavar="ROLE=EFFORT",
        help="Override a role effort; repeat for multiple roles.",
    )
    parser.add_argument(
        "--external-provider",
        choices=("none", "anthropic", "deepseek"),
        default=None,
        help="Optionally configure a read-only Anthropic or DeepSeek proposal bridge.",
    )
    parser.add_argument("--external-model", default=None)
    parser.add_argument(
        "--external-effort",
        choices=("auto", "none", "minimal", "low", "medium", "high", "xhigh", "max"),
        default=None,
    )
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Back up and replace conflicting managed agent, skill, or tool files.",
    )
    parser.add_argument(
        "--force-config",
        action="store_true",
        help="Back up and replace an existing .codex/config.toml.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove only files owned by this installer and the managed AGENTS.md block.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console_output()
    if sys.version_info < (3, 11):
        print("install error: Python 3.11 or newer is required.", file=sys.stderr)
        return 2

    args = build_parser().parse_args(argv)
    try:
        root = source_root()
        target = validate_target(args.target, root)
        if args.uninstall:
            return uninstall(target, args.dry_run)
        preset = args.preset
        external_provider = args.external_provider
        model_overrides = list(args.role_model)
        effort_overrides = list(args.role_effort)
        external_model = args.external_model
        external_effort = args.external_effort
        if args.interactive:
            (
                preset,
                model_overrides,
                effort_overrides,
                external_provider,
                external_model,
                external_effort,
            ) = interactive_options(
                preset,
                model_overrides,
                effort_overrides,
                external_provider,
                external_model,
                external_effort,
            )
        preset = preset or "balanced"
        external_provider = external_provider or "none"
        settings = resolve_profile(
            preset, args.profile, model_overrides, effort_overrides
        )
        external_model, external_effort = validate_external_selection(
            external_provider, external_model, external_effort
        )
        return install(
            target=target,
            preset=preset,
            settings=settings,
            external_provider=external_provider,
            external_model=external_model,
            external_effort=external_effort,
            force=args.force,
            force_config=args.force_config,
            dry_run=args.dry_run,
        )
    except (InstallError, OSError) as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
