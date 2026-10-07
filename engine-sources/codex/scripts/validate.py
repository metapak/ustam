#!/usr/bin/env python3
"""Static repository validation for Ustam."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_AGENTS: dict[str, dict[str, str]] = {
    "acceptance-test-author.toml": {"name": "acceptance_test_author", "model": "gpt-6.1-sol", "effort": "medium", "sandbox": "workspace-write"},
    "fast-lookup.toml": {
        "name": "fast_lookup",
        "model": "gpt-6-luna",
        "effort": "medium",
        "sandbox": "read-only",
    },
    "explorer.toml": {
        "name": "explorer",
        "model": "gpt-6-luna",
        "effort": "high",
        "sandbox": "read-only",
    },
    "researcher.toml": {
        "name": "researcher",
        "model": "gpt-6.1-sol",
        "effort": "medium",
        "sandbox": "read-only",
    },
    "implementer.toml": {
        "name": "implementer",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "sandbox": "workspace-write",
    },
    "verifier.toml": {
        "name": "verifier",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "sandbox": "workspace-write",
    },
    "failure-analyst.toml": {
        "name": "failure_analyst",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "sandbox": "read-only",
    },
    "qa-operator.toml": {
        "name": "qa_operator",
        "model": "gpt-6.1-sol",
        "effort": "medium",
        "sandbox": "workspace-write",
    },
    "reviewer.toml": {
        "name": "reviewer",
        "model": "gpt-6-astra",
        "effort": "medium",
        "sandbox": "read-only",
    },
    "advisor.toml": {
        "name": "advisor",
        "model": "gpt-6-astra",
        "effort": "xhigh",
        "sandbox": "read-only",
    },
}

REQUIRED_FILES = (
    "scripts/model_catalog.py",
    "scripts/model_catalog.json",
    "scripts/dashboard.py",
    "scripts/console/index.html",
    "scripts/console/app.js",
    "scripts/console/style.css",
    "docs/local-console.md",
    "docs/local-console.tr.md",
    "tests/test_dashboard.py",
    "tests/fixtures/usage-sanitized/rollout.jsonl",
    "tests/fixtures/usage-sanitized/rollout-two.jsonl",
    "tests/fixtures/usage-sanitized/rollout-helper-one.jsonl",
    "tests/fixtures/usage-sanitized/rollout-helper-two.jsonl",
    "tests/fixtures/usage-sanitized/rollout-helper-three.jsonl",
    "tests/fixtures/usage-sanitized/rollout-helper-four.jsonl",
    "tests/fixtures/usage-sanitized/rollout-unknown.jsonl",
    "tests/fixtures/usage-sanitized/style-history.json",
    "scripts/console/orchestra.svg",
    ".codex/config.toml",
    "presets/sol-owner.config.toml",
    ".codex/tools/candidate.py",
    ".codex/tools/ledger.py",
    ".codex/tools/usage_report.py",
    ".codex/tools/local_eval.py",
    ".codex/bounded-orchestrator.eval.example.json",
    ".codex/tools/anthropic_mcp.py",
    ".codex/tools/deepseek_mcp.py",
    ".codex/.candidate/.gitignore",
    ".codex/.bounded-orchestrator/.gitignore",
    ".agents/skills/bounded-orchestrator/SKILL.md",
    ".agents/skills/bounded-orchestrator/references/task-contract.md",
    ".agents/skills/bounded-orchestrator/references/review-protocol.md",
    ".agents/skills/bounded-orchestrator/references/escalation.md",
    ".agents/skills/bounded-orchestrator-ui-design/SKILL.md",
    ".agents/skills/bounded-orchestrator-security-review/SKILL.md",
    "templates/AGENTS.block.md",
    "scripts/install.py",
    "scripts/install.sh",
    "scripts/install.ps1",
    "setup.command",
    "setup.ps1",
    "setup.cmd",
    "INSTALL-MACOS.md",
    "INSTALL-WINDOWS.md",
    "README.md",
    "README.tr.md",
    "docs/architecture.md",
    "docs/from-astra-luna-orchestrator.md",
    "docs/runtime-smoke-test.md",
    "docs/task-ledger.md",
    "docs/task-ledger.tr.md",
    "docs/expertise-packs.md",
    "docs/expertise-packs.tr.md",
    "docs/external-providers.md",
    "docs/external-providers.tr.md",
    "docs/profiles.md",
    "docs/profiles.tr.md",
    "docs/release-v0.3.0.md",
    "docs/release-v0.3.0.tr.md",
    "docs/release-v0.4.0.md",
    "docs/release-v0.4.0.tr.md",
    "docs/release-v0.4.1.md",
    "docs/release-v0.4.1.tr.md",
    "docs/release-v0.5.0.md",
    "docs/release-v0.5.0.tr.md",
    "docs/release-v0.6.0.md",
    "docs/release-v0.6.0.tr.md",
    "docs/usage-and-local-eval.md",
    "docs/usage-and-local-eval.tr.md",
    "AGENTS.md",
    "LICENSE",
    "NOTICE",
    "VERSION",
    ".gitattributes",
)


def load_toml(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"invalid TOML {path.relative_to(ROOT)}: {exc}")
        return {}


def validate_root_config(
    path: Path,
    expected_model: str,
    expected_effort: str,
    errors: list[str],
) -> None:
    config = load_toml(path, errors)
    if not config:
        return

    expected = {
        "model": expected_model,
        "model_reasoning_effort": expected_effort,
        "review_model": "gpt-6-astra",
        "approval_policy": "on-request",
        "sandbox_mode": "workspace-write",
    }
    for key, value in expected.items():
        if config.get(key) != value:
            errors.append(
                f"{path.relative_to(ROOT)}: expected {key}={value!r}, "
                f"got {config.get(key)!r}"
            )

    agents = config.get("agents", {})
    if agents.get("enabled") is not True:
        errors.append(f"{path.relative_to(ROOT)}: root agents.enabled must be true")
    if agents.get("max_concurrent_threads_per_session") != 4:
        errors.append(
            f"{path.relative_to(ROOT)}: "
            "max_concurrent_threads_per_session must be 4"
        )
    if agents.get("max_depth") != 1:
        errors.append(f"{path.relative_to(ROOT)}: max_depth must be 1")
    if agents.get("default_subagent_model") != "gpt-6-luna":
        errors.append(f"{path.relative_to(ROOT)}: default subagent must be Luna")
    if agents.get("default_subagent_reasoning_effort") != "high":
        errors.append(
            f"{path.relative_to(ROOT)}: default subagent effort must be medium"
        )
    if agents.get("interrupt_message") is not True:
        errors.append(f"{path.relative_to(ROOT)}: interrupt_message must be true")

    expected_names = {values["name"] for values in EXPECTED_AGENTS.values()}
    role_names = {key for key, value in agents.items() if isinstance(value, dict)}
    if role_names != expected_names:
        errors.append(
            f"{path.relative_to(ROOT)}: role registrations differ; "
            f"expected {sorted(expected_names)}, got {sorted(role_names)}"
        )

    for filename, values in EXPECTED_AGENTS.items():
        name = values["name"]
        role = agents.get(name, {})
        expected_file = f"./agents/{filename}"
        if role.get("config_file") != expected_file:
            errors.append(
                f"{path.relative_to(ROOT)}: agents.{name}.config_file "
                f"must be {expected_file!r}"
            )
        if not isinstance(role.get("description"), str) or not role.get(
            "description", ""
        ).strip():
            errors.append(f"{path.relative_to(ROOT)}: agents.{name} needs a description")


def validate_agents(errors: list[str]) -> None:
    names: set[str] = set()
    directory = ROOT / ".codex/agents"
    actual = {path.name for path in directory.glob("*.toml")}
    expected = set(EXPECTED_AGENTS)

    for missing in sorted(expected - actual):
        errors.append(f"missing agent file: .codex/agents/{missing}")
    for unexpected in sorted(actual - expected):
        errors.append(f"unexpected agent file: .codex/agents/{unexpected}")

    for filename, expected_values in EXPECTED_AGENTS.items():
        path = directory / filename
        if not path.exists():
            continue
        config = load_toml(path, errors)
        if not config:
            continue

        for required in ("name", "description", "developer_instructions"):
            if not isinstance(config.get(required), str) or not config.get(
                required, ""
            ).strip():
                errors.append(f"{path.relative_to(ROOT)}: missing non-empty {required}")

        name = config.get("name")
        if name in names:
            errors.append(f"duplicate agent name: {name}")
        if isinstance(name, str):
            names.add(name)

        if name != expected_values["name"]:
            errors.append(f"{path.relative_to(ROOT)}: unexpected name {name!r}")
        if config.get("model") != expected_values["model"]:
            errors.append(
                f"{path.relative_to(ROOT)}: expected model "
                f"{expected_values['model']!r}, got {config.get('model')!r}"
            )
        if config.get("model_reasoning_effort") != expected_values["effort"]:
            errors.append(
                f"{path.relative_to(ROOT)}: expected reasoning effort "
                f"{expected_values['effort']!r}, "
                f"got {config.get('model_reasoning_effort')!r}"
            )
        if config.get("sandbox_mode") != expected_values["sandbox"]:
            errors.append(
                f"{path.relative_to(ROOT)}: expected sandbox mode "
                f"{expected_values['sandbox']!r}, got {config.get('sandbox_mode')!r}"
            )
        if config.get("agents", {}).get("enabled") is not False:
            errors.append(f"{path.relative_to(ROOT)}: child agents.enabled must be false")

        instructions = config.get("developer_instructions", "").lower()
        if not any(term in instructions for term in ("delegate", "delegation")):
            errors.append(
                f"{path.relative_to(ROOT)}: instructions must explicitly prohibit delegation"
            )


def validate_skill(errors: list[str]) -> None:
    path = ROOT / ".agents/skills/bounded-orchestrator/SKILL.md"
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append("SKILL.md: missing YAML front matter")
        return
    try:
        _, front_matter, _ = text.split("---", 2)
    except ValueError:
        errors.append("SKILL.md: malformed YAML front matter")
        return
    if "name: bounded-orchestrator" not in front_matter:
        errors.append("SKILL.md: unexpected skill name")

    required_phrases = (
        "one writer per file",
        "Freeze the candidate",
        "no recursive delegation",
        "Maximum writer turns",
        "External-effect boundary",
        "GPT-6 Luna",
        "GPT-6.1 Sol",
        "GPT-6 Astra medium",
    )
    lowered = text.lower()
    for phrase in required_phrases:
        if phrase.lower() not in lowered:
            errors.append(f"SKILL.md: missing invariant phrase {phrase!r}")


def validate_expertise_packs(errors: list[str]) -> None:
    packs = {
        ".agents/skills/bounded-orchestrator-ui-design/SKILL.md": (
            "name: bounded-orchestrator-ui-design",
            ("opt-in", "grants no", "one writer", "accessibility"),
        ),
        ".agents/skills/bounded-orchestrator-security-review/SKILL.md": (
            "name: bounded-orchestrator-security-review",
            ("opt-in", "grants no", "one writer", "trust boundaries"),
        ),
    }
    for relative, (name, phrases) in packs.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        if not text.startswith("---\n") or name not in text.split("---", 2)[1]:
            errors.append(f"{relative}: invalid skill front matter")
        lowered = text.lower()
        for phrase in phrases:
            if phrase not in lowered:
                errors.append(f"{relative}: missing bounded phrase {phrase!r}")


def validate_markers(errors: list[str]) -> None:
    path = ROOT / "templates/AGENTS.block.md"
    text = path.read_text(encoding="utf-8")
    start = "<!-- codex-bounded-orchestrator:start -->"
    end = "<!-- codex-bounded-orchestrator:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        errors.append("templates/AGENTS.block.md: managed markers must appear once")
    if text.find(start) > text.find(end):
        errors.append("templates/AGENTS.block.md: managed markers are reversed")


def validate_runtime_ignores(errors: list[str]) -> None:
    for relative in (
        Path(".codex/.candidate/.gitignore"),
        Path(".codex/.bounded-orchestrator/.gitignore"),
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        if text != "*\n!.gitignore\n":
            errors.append(f"{relative}: runtime directory must ignore everything else")


def validate_wrapper_modes(errors: list[str]) -> None:
    # Publication fix: Windows does not expose POSIX executable permission bits.
    # Archive permissions remain checked by the release-package tests.
    if sys.platform == "win32":
        return
    for relative in (
        Path("setup.command"),
        Path("scripts/install.sh"),
        Path("scripts/install.py"),
        Path("scripts/validate.py"),
        Path("scripts/build_release.py"),
        Path(".codex/tools/ledger.py"),
        Path(".codex/tools/usage_report.py"),
        Path(".codex/tools/local_eval.py"),
        Path(".codex/tools/anthropic_mcp.py"),
        Path(".codex/tools/deepseek_mcp.py"),
    ):
        if not ((ROOT / relative).stat().st_mode & 0o111):
            errors.append(f"{relative}: expected executable bit")


def main() -> int:
    if sys.version_info < (3, 11):
        print("ERROR: Python 3.11 or newer is required.", file=sys.stderr)
        return 2

    errors: list[str] = []
    if (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "0.6.0":
        errors.append("VERSION must be 0.6.0")
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required file: {relative}")

    if not errors:
        validate_root_config(
            ROOT / ".codex/config.toml", "gpt-6-astra", "medium", errors
        )
        validate_root_config(
            ROOT / "presets/sol-owner.config.toml", "gpt-6.1-sol", "high", errors
        )
        validate_agents(errors)
        validate_skill(errors)
        validate_expertise_packs(errors)
        validate_markers(errors)
        validate_runtime_ignores(errors)
        validate_wrapper_modes(errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"Repository validation passed for {len(EXPECTED_AGENTS)} "
        "registered agent profiles."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
