#!/usr/bin/env python3
"""Validate repository invariants without third-party dependencies."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = {"acceptance-test-author", "explorer", "researcher", "implementer", "verifier", "failure-analyst", "qa-operator", "reviewer", "advisor"}
EXPECTED_ROUTING = {
    "acceptance-test-author": ("sonnet", "medium"),
    "explorer": ("sonnet", "medium"),
    "researcher": ("sonnet", "medium"),
    "implementer": ("sonnet", "high"),
    "verifier": ("sonnet", "high"),
    "qa-operator": ("sonnet", "high"),
    "failure-analyst": ("opus", "high"),
    "reviewer": ("opus", "high"),
    "advisor": ("opus", "xhigh"),
}

CLAUDE_MODEL_ALIASES = {"opus", "sonnet", "haiku", "fable"}


def is_native_model(model: str) -> bool:
    return model in CLAUDE_MODEL_ALIASES or model.startswith("claude-")


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("missing YAML frontmatter")
    raw = text.split("\n---\n", 1)[0][4:]
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def main() -> int:
    errors: list[str] = []
    required = ["README.md", "README.tr.md", "LICENSE", "NOTICE", "CLAUDE.md", ".claude/settings.json", ".claude/tools/task_ledger.py", ".claude/tools/usage_report.py", ".claude/tools/local_eval.py", ".claude/bounded-orchestrator.eval.example.json", ".claude/tools/openai_mcp.py", ".claude/tools/deepseek_mcp.py", "docs/usage-and-local-eval.md", "docs/usage-and-local-eval.tr.md", "docs/release-v0.5.0.md", "docs/release-v0.5.0.tr.md", "scripts/install.py", "scripts/build_release.py", "scripts/configure.py", ".claude/tools/console_settings.py", ".claude/tools/console/index.html", ".claude/tools/console/app.js", ".claude/tools/console/style.css"]
    for name in required:
        if not (ROOT / name).is_file():
            errors.append(f"missing {name}")
    try:
        settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        if settings.get("model") != "opus":
            errors.append("main owner model must be opus")
        if settings.get("effortLevel") != "xhigh":
            errors.append("main owner effortLevel must be xhigh")
        if settings.get("env", {}).get("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH") != "1":
            errors.append("subagent spawn depth must be the string '1'")
    except Exception as exc:
        errors.append(f"invalid settings: {exc}")
    names: set[str] = set()
    actual = {path.stem for path in (ROOT / ".claude/agents").glob("*.md")}
    if actual != AGENTS:
        errors.append(f"agent set differs: {sorted(actual)}")
    for path in sorted((ROOT / ".claude/agents").glob("*.md")):
        try:
            data = frontmatter(path)
            name = data.get("name", "")
            if not name or name in names:
                errors.append(f"non-unique name in {path.name}")
            names.add(name)
            expected_model, expected_effort = EXPECTED_ROUTING[path.stem]
            if data.get("model") != expected_model:
                errors.append(
                    f"{path.name} model must be {expected_model}, got {data.get('model')}"
                )
            if data.get("effort") != expected_effort:
                errors.append(
                    f"{path.name} effort must be {expected_effort}, got {data.get('effort')}"
                )
            tools = {item.strip() for item in data.get("tools", "").split(",")}
            writers = {"Edit", "Write"}.intersection(tools)
            if path.stem == "implementer":
                if writers != {"Edit", "Write"}:
                    errors.append("implementer must have Edit and Write")
            elif writers:
                errors.append(f"{path.name} must not have Edit or Write")
            if "Agent" not in data.get("disallowedTools", "").split(", "):
                errors.append(f"{path.name} must disallow Agent")
        except Exception as exc:
            errors.append(f"invalid {path.name}: {exc}")
    for skill in (ROOT / ".claude/skills").glob("*/SKILL.md"):
        data = frontmatter(skill)
        if data.get("disable-model-invocation") != "true":
            errors.append(f"{skill}: skill must be opt-in")
        if "tools" in data or "allowed-tools" in data:
            errors.append(f"{skill}: expertise must not grant tools")
    for doc in [ROOT / "README.md", ROOT / "README.tr.md"]:
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" not in target and not (doc.parent / target).resolve().exists():
                errors.append(f"broken local link in {doc.name}: {target}")
    for path in (ROOT / ".claude/agents").glob("*.md"):
        model = frontmatter(path).get("model", "")
        if not is_native_model(model):
            errors.append(f"{path.name}: native route must use an Anthropic Claude model")
    if (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "0.5.0":
        errors.append("VERSION must be 0.5.0")
    if errors:
        print("Repository validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
