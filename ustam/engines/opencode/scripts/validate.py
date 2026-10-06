#!/usr/bin/env python3
"""Validate the OpenCode V2 bounded-orchestrator distribution."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ROLES=("owner","fast-lookup","explorer","researcher","acceptance-test-author","implementer","verifier","failure-analyst","qa-operator","reviewer","advisor")
CHILDREN=ROLES[1:]
V1_KEYS={"agent","tools","max_depth","model_reasoning_effort","sandbox_mode","approval_policy"}
REQUIRED=("README.md","README.tr.md","LICENSE","NOTICE","SECURITY.md","CONTRIBUTING.md","CHANGELOG.md","VERSION","INSTALL-MACOS-LINUX.md","INSTALL-WINDOWS.md","docs/architecture.md","docs/profiles.md","docs/profiles.tr.md","docs/usage-and-local-eval.md","docs/usage-and-local-eval.tr.md","docs/task-ledger.md","docs/task-ledger.tr.md","docs/roadmap.md","docs/roadmap.tr.md","docs/release-v0.1.0.md","docs/release-v0.1.0.tr.md","docs/assets/opencode-bounded-orchestrator-cover-en.svg","docs/assets/opencode-bounded-orchestrator-cover-tr.svg","docs/local-console.md",".opencode/tools/console.py",".opencode/tools/team_editor.py",".opencode/tools/console.html",".opencode/tools/console.js",".opencode/tools/console.css",".opencode/tools/orchestra-actors.svg","scripts/install.py","scripts/dashboard.py","scripts/smoke_opencode.py","scripts/build_release.py","setup.command","setup.ps1","setup.cmd","launchers/Ustam.app/Contents/Info.plist","launchers/Ustam.app/Contents/MacOS/launch","launchers/launch_dashboard.py","launchers/Launch Ustam.vbs",".github/workflows/ci.yml")


def permissions(agent: dict, action: str) -> list[dict]:
    return [item for item in agent.get("permissions",[]) if isinstance(item,dict) and item.get("action")==action]


def valid_permission_rule(rule: object) -> bool:
    return isinstance(rule,dict) and set(rule)=={"action","resource","effect"} and rule.get("effect") in {"allow","ask","deny"}


def main() -> int:
    errors=[]
    try: config=json.loads((ROOT/".opencode/opencode.jsonc").read_text(encoding="utf-8"))
    except Exception as exc: errors.append(f"invalid .opencode/opencode.jsonc: {exc}"); config={}
    if config.get("default_agent")!="owner": errors.append("default_agent must be owner")
    agents=config.get("agents")
    if not isinstance(agents,dict) or set(agents)!=set(ROLES): errors.append("agents must contain the exact bounded role set"); agents=agents if isinstance(agents,dict) else {}
    if agents.get("owner",{}).get("mode")!="primary": errors.append("owner must be primary")
    if "model" in config: errors.append("default distribution must inherit the current session model")
    for role in ROLES:
        agent=agents.get(role,{})
        if "model" in agent: errors.append(f"{role} must omit model by default")
        if not isinstance(agent.get("steps"),int) or agent.get("steps",0)<1: errors.append(f"{role} needs a finite positive steps budget")
        rules=agent.get("permissions",[])
        for index, rule in enumerate(rules):
            if not valid_permission_rule(rule): errors.append(f"{role} permission rule {index} must use action/resource/effect with a valid effect")
        edits=permissions(agent,"edit")
        allows=any(item.get("effect")=="allow" for item in edits)
        if (role=="implementer") != allows: errors.append(f"only implementer may allow edit: {role}")
        if role in CHILDREN:
            subs=permissions(agent,"subagent")
            if not subs or subs[-1].get("resource")!="*" or subs[-1].get("effect")!="deny": errors.append(f"{role} must end with catch-all subagent deny")
            if not rules or rules[-1] is not subs[-1]: errors.append(f"{role} final permission must deny all subagents")
            if agent.get("mode")!="subagent": errors.append(f"{role} must be subagent")
    owner_rules=agents.get("owner",{}).get("permissions",[])
    expected=[("subagent",role,"allow") for role in CHILDREN]+[("skill","bounded-orchestrator","allow"),("question","*","allow")]
    if not owner_rules or owner_rules[0] != {"action":"*","resource":"*","effect":"deny"}:
        errors.append("owner must begin with catch-all tool deny")
    if [(item.get("action"),item.get("resource"),item.get("effect")) for item in owner_rules[2:]] != expected:
        errors.append("owner must allow only named specialists, orchestration skill, and user questions")
    if len(owner_rules)<2 or owner_rules[1] != {"action":"subagent","resource":"*","effect":"deny"}:
        errors.append("owner must deny arbitrary subagents before named allows")
    owner_sub=permissions(agents.get("owner",{}),"subagent")
    allowed={item.get("resource") for item in owner_sub if item.get("effect")=="allow"}
    if set(CHILDREN)-allowed: errors.append("owner subagent allowlist is incomplete")
    catch=[index for index,item in enumerate(owner_sub) if item.get("resource")=="*" and item.get("effect")=="deny"]
    if not catch or any(index <= catch[-1] for index,item in enumerate(owner_sub) if item.get("effect")=="allow"): errors.append("owner catch-all subagent deny must precede every named allow")
    for role in ("fast-lookup","explorer","researcher","failure-analyst","reviewer","advisor"):
        if not any(item.get("resource")=="*" and item.get("effect")=="deny" for item in permissions(agents.get(role,{}),"shell")): errors.append(f"{role} must deny shell")
    for role in ("verifier","qa-operator"):
        shell=permissions(agents.get(role,{}),"shell")
        broad=next((i for i,item in enumerate(shell) if item.get("resource")=="*" and item.get("effect")=="ask"),None)
        narrow=[i for i,item in enumerate(shell) if item.get("resource")!="*" and item.get("effect")=="allow"]
        if broad is None or not narrow or any(i <= broad for i in narrow): errors.append(f"{role} broad shell ask must precede narrow git allows")
    raw=(ROOT/".opencode/opencode.jsonc").read_text(encoding="utf-8") if (ROOT/".opencode/opencode.jsonc").exists() else ""
    for key in V1_KEYS:
        if re.search(rf'"{re.escape(key)}"\s*:',raw): errors.append(f"V1 key is forbidden: {key}")
    for role in ROLES:
        path=ROOT/f".opencode/agents/{role}.md"
        if not path.is_file(): errors.append(f"missing agent file: {path.relative_to(ROOT)}"); continue
        text=path.read_text(encoding="utf-8")
        frontmatter=text.split("---",2)[1] if text.count("---")>=2 else ""
        if "pattern:" in frontmatter or "permission:" in frontmatter: errors.append(f"{role} agent file uses obsolete permission item keys")
        if "resource:" not in frontmatter or "effect:" not in frontmatter: errors.append(f"{role} agent file must use resource/effect")
        if role in CHILDREN and not re.search(r'action:\s*subagent,\s*resource:\s*"?\*"?,\s*effect:\s*deny\s*}\s*$',frontmatter,re.MULTILINE): errors.append(f"{role} file must end with subagent deny")
        if re.search(r"^(model|steps):",text,re.M): errors.append(f"{role} scalar model/steps must live only in JSON config")
    owner_text=(ROOT/".opencode/agents/owner.md").read_text(encoding="utf-8")
    if owner_text.index('resource: "*"',owner_text.index("action: subagent")) > owner_text.index("resource: fast-lookup"): errors.append("owner Markdown catch-all deny must precede named subagent allows")
    owner_front=owner_text.split("---",2)[1]
    if not re.search(r'action:\s*"\*",\s*resource:\s*"\*",\s*effect:\s*deny',owner_front): errors.append("owner Markdown must deny all tools first")
    if 'action: skill, resource: bounded-orchestrator, effect: allow' not in owner_front or 'action: question, resource: "*", effect: allow' not in owner_front: errors.append("owner Markdown must match limited skill and question allows")
    if re.search(r'action:\s*(?:read|edit|shell|grep|glob|webfetch|websearch|execute),[^\n]*effect:\s*(?:ask|allow)',owner_front): errors.append("owner Markdown may not allow execution tools")
    for role in ("verifier","qa-operator"):
        text=(ROOT/f".opencode/agents/{role}.md").read_text(encoding="utf-8")
        if text.index('resource: "*", effect: ask') > text.index('resource: "git status*", effect: allow'): errors.append(f"{role} Markdown broad shell ask must precede narrow allow")
    for name in REQUIRED:
        if not (ROOT/name).is_file(): errors.append(f"missing required file: {name}")
    for path in [*ROOT.glob("scripts/*.py"),*ROOT.glob(".opencode/tools/*.py")]:
        try: ast.parse(path.read_text(encoding="utf-8"),filename=str(path),feature_version=(3,10))
        except SyntaxError as exc: errors.append(f"Python 3.10 syntax incompatibility in {path.relative_to(ROOT)}: {exc}")
    attribution=(ROOT/"NOTICE").read_text(encoding="utf-8")+"\n"+(ROOT/"docs/provenance.md").read_text(encoding="utf-8")
    for value in ("codex-astra-luna-orchestrator","donvito","https://github.com/donvito/codex-astra-luna-orchestrator","Apache License, Version 2.0","not affiliated with or endorsed"):
        if value not in attribution: errors.append(f"missing inherited attribution: {value}")
    version=(ROOT/"VERSION").read_text().strip() if (ROOT/"VERSION").exists() else ""
    if version!="0.1.0": errors.append("VERSION must be 0.1.0")
    if errors:
        print("VALIDATION FAILED")
        for error in errors: print(f"- {error}")
        return 1
    print("VALIDATION PASSED: OpenCode V2 config, bounded roles, docs, and packaging files are consistent.")
    return 0


if __name__=="__main__": raise SystemExit(main())
