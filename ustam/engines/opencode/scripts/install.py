#!/usr/bin/env python3
"""Safely install Ustam into a repository."""

from __future__ import annotations

import argparse
import copy
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(".opencode/.bounded-orchestrator/install.json")
BACKUPS = Path(".opencode/.bounded-orchestrator/backups")
START = "<!-- opencode-bounded-orchestrator:start -->"
END = "<!-- opencode-bounded-orchestrator:end -->"
ROLES = ("owner", "fast-lookup", "explorer", "researcher", "acceptance-test-author", "implementer", "verifier", "failure-analyst", "qa-operator", "reviewer", "advisor")
TEAM_ROLES = ROLES[1:]
MAX_HELPERS = 50
SLOT = re.compile(r"\.opencode/agents/helper-(?:0[1-9]|[1-4][0-9]|50)\.md\Z")
DUTY = re.compile(r"[A-Za-z0-9À-ž _.,:;!?()/-]{0,80}\Z")
SELECTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._/-]*(?:#[A-Za-z0-9][A-Za-z0-9._-]*)?$")
PROFILES = {
    "balanced": {"owner":36,"fast-lookup":10,"explorer":22,"researcher":22,"implementer":34,"verifier":20,"failure-analyst":22,"qa-operator":20,"reviewer":22,"advisor":24},
    "quality": {"owner":56,"fast-lookup":16,"explorer":34,"researcher":34,"implementer":52,"verifier":32,"failure-analyst":34,"qa-operator":32,"reviewer":36,"advisor":40},
    "economy": {"owner":24,"fast-lookup":7,"explorer":14,"researcher":14,"implementer":22,"verifier":13,"failure-analyst":14,"qa-operator":13,"reviewer":14,"advisor":16},
    "quota-saver": {"owner":18,"fast-lookup":5,"explorer":10,"researcher":10,"implementer":16,"verifier":9,"failure-analyst":10,"qa-operator":9,"reviewer":10,"advisor":12},
}
for _profile in PROFILES.values():
    _profile["acceptance-test-author"] = _profile["verifier"]

MANAGED = [Path(".opencode/opencode.jsonc"), Path(".opencode/bounded-orchestrator.eval.example.json"), Path(".opencode/.candidate/.gitignore"), Path(".opencode/.bounded-orchestrator/.gitignore")]
MANAGED += [Path(f".opencode/agents/{role}.md") for role in ROLES]
MANAGED += [Path(".opencode/tools") / name for name in ("work_protocol.py","work_protocol_core.py","work_protocol","candidate.py","ledger.py","usage_report.py","local_eval.py","console.py","team_editor.py","console.html","console.css","console.js","orchestra-actors.svg")]
MANAGED += [Path(".opencode/skills/bounded-orchestrator/SKILL.md"), Path(".opencode/skills/bounded-orchestrator/references/task-contract.md"), Path(".opencode/skills/bounded-orchestrator/references/review-protocol.md"), Path(".opencode/skills/bounded-orchestrator/references/escalation.md")]
ALLOWED_MANIFEST_FILES={path.as_posix() for path in MANAGED}
IGNORE_SENTINELS={Path(".opencode/.candidate/.gitignore"),Path(".opencode/.bounded-orchestrator/.gitignore")}


def work_protocol_wrapper(target):
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--work-protocol', 'opencode']
    else:
        command = [sys.executable, str((target / '.opencode/tools/work_protocol.py').resolve())]
    command += ['--project', str(target.resolve())]
    return '#!/bin/sh\n# Fixed project and installer runtime; no paid call.\nexec ' + ' '.join(shlex.quote(a) for a in command) + ' "$@"\n'


class InstallError(RuntimeError): pass


def ensure_safe_parent(target: Path, relative: Path) -> None:
    current = target
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise InstallError(f"Refusing symlinked parent path: {current.relative_to(target)}")
        if current.exists() and not current.is_dir():
            raise InstallError(f"Refusing non-directory parent path: {current.relative_to(target)}")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists(): tmp.unlink()


def _uninstall_staged(target: Path, manifest: dict, planned: list, agents_before: bytes | None, cleaned: bytes | None, manifest_before: bytes | None) -> None:
    """Move owned files to a private staging directory before discarding them."""
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    root_fd=os.open(target,flags)
    opened=[];staged=[];created=[];stage_fd=None;stage_name=None;runtime_fd=None
    def directory_fd(relative: Path) -> int:
        fd=os.dup(root_fd);opened.append(fd)
        for part in relative.parts:
            if part=='.': continue
            next_fd=os.open(part,flags,dir_fd=fd)
            opened.append(next_fd);fd=next_fd
        return fd
    def bytes_at(parent_fd: int, name: str) -> bytes:
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise InstallError(f'Path changed during uninstall: {name}')
            with os.fdopen(fd,'rb',closefd=False) as stream: return stream.read()
        finally: os.close(fd)
    def stage(relative: Path, expected: bytes | None = None, checksum: str | None = None) -> None:
        parent_fd=directory_fd(relative.parent);name=f'file-{len(staged):03d}'
        os.rename(relative.name,name,src_dir_fd=parent_fd,dst_dir_fd=stage_fd)
        staged.append((parent_fd,relative.name,name))
        actual=bytes_at(stage_fd,name)
        if (expected is not None and actual!=expected) or (checksum is not None and hashlib.sha256(actual).hexdigest()!=checksum):
            raise InstallError(f'Path changed during uninstall: {relative}')
    def create(parent_fd: int, name: str, data: bytes) -> None:
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent_fd)
        identity=os.fstat(fd).st_ino
        created.append((parent_fd,name,identity,data))
        try:
            with os.fdopen(fd,'wb',closefd=False) as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        finally: os.close(fd)
    def restore_no_replace(staged_name: str, parent_fd: int, name: str) -> None:
        # link() fails with EEXIST if another writer creates the destination.
        # Both directories are on the same filesystem, so the staged inode is
        # still available if the restore cannot finish.
        os.link(staged_name,name,src_dir_fd=stage_fd,dst_dir_fd=parent_fd,follow_symlinks=False)
        os.unlink(staged_name,dir_fd=stage_fd)
    try:
        runtime_fd=directory_fd(BACKUPS.parent)
        stage_name='uninstall-recovery-'+secrets.token_hex(12)
        os.mkdir(stage_name,0o700,dir_fd=runtime_fd)
        stage_fd=os.open(stage_name,flags,dir_fd=runtime_fd)
        for kind,relative,_ in planned:
            if kind=='remove': stage(relative,checksum=manifest['files'][relative.as_posix()]['sha256'])
            elif kind=='sentinel':
                parent_fd=directory_fd(relative.parent)
                try: sentinel_stat=os.stat(relative.name,dir_fd=parent_fd,follow_symlinks=False)
                except FileNotFoundError: create(parent_fd,relative.name,(ROOT/relative).read_bytes())
                else:
                    if not stat.S_ISREG(sentinel_stat.st_mode): raise InstallError(f'Path changed during uninstall: {relative}')
        if cleaned is not None:
            stage(Path('AGENTS.md'),expected=agents_before)
            create(root_fd,'AGENTS.md',cleaned)
        if manifest_before is not None: stage(MANIFEST,expected=manifest_before)
        # Retain the staged inodes privately: an already-open file handle may
        # receive a user edit even after the last checksum check.
        staged.clear()
    except Exception as exc:
        failed=[]
        for index,(parent_fd,name,identity,expected) in reversed(list(enumerate(created))):
            held_name=f'created-{index:03d}';moved=False
            try:
                os.rename(name,held_name,src_dir_fd=parent_fd,dst_dir_fd=stage_fd);moved=True
                if os.stat(held_name,dir_fd=stage_fd,follow_symlinks=False).st_ino!=identity or bytes_at(stage_fd,held_name)!=expected:
                    raise InstallError('Created file changed outside this uninstall.')
                moved=False  # Keep the recovery inode until the user reviews the failure.
            except Exception as rollback_exc: failed.append(f'{name}: {rollback_exc}')
            finally:
                if moved:
                    try:
                        try: os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
                        except FileNotFoundError: pass
                        else: raise InstallError('Original path changed outside this uninstall.')
                        restore_no_replace(held_name,parent_fd,name)
                    except Exception as restore_exc: failed.append(f'{name} retained in private staging: {restore_exc}')
        for parent_fd,name,staged_name in reversed(staged):
            try:
                try: os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
                except FileNotFoundError: pass
                else: raise InstallError('Original path changed outside this uninstall.')
                restore_no_replace(staged_name,parent_fd,name)
            except Exception as rollback_exc: failed.append(f'{name}: {rollback_exc}')
        if failed: raise InstallError('Uninstall failed; rollback incomplete: '+', '.join(failed)) from exc
        raise
    finally:
        if stage_fd is not None: os.close(stage_fd)
        if stage_name is not None and runtime_fd is not None:
            try: os.rmdir(stage_name,dir_fd=runtime_fd)
            except OSError: pass  # Keep staged bytes available if rollback could not finish.
        for fd in reversed(opened): os.close(fd)
        os.close(root_fd)


def load_manifest(target: Path) -> dict[str, Any]:
    ensure_safe_parent(target, MANIFEST)
    path = target / MANIFEST
    if path.is_symlink() or (path.exists() and not path.is_file()): raise InstallError("Refusing unsafe install manifest")
    if not path.exists(): return {"schema":1,"files":{},"agents_block":False}
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise InstallError(f"Cannot read manifest: {exc}") from exc
    if data.get("schema") != 1 or not isinstance(data.get("files"), dict): raise InstallError("Unsupported install manifest")
    if any(name not in ALLOWED_MANIFEST_FILES and not SLOT.fullmatch(name) for name in data["files"]): raise InstallError("Install manifest contains an unmanaged path")
    block_hash=data.get('agents_block_sha256')
    if block_hash is not None and (not isinstance(block_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',block_hash)):
        raise InstallError('Install manifest contains an invalid AGENTS.md block checksum')
    validate_team(data.get("team",[]))
    if any(not isinstance(meta,dict) or not re.fullmatch(r"[0-9a-f]{64}",str(meta.get("sha256",""))) for meta in data["files"].values()): raise InstallError("Install manifest contains an invalid checksum")
    return data


def selector(value: str) -> str:
    if not SELECTOR.fullmatch(value): raise InstallError(f"Invalid model selector {value!r}; expected provider/model or provider/model#variant")
    return value


def provider(value: str) -> str: return value.split("/", 1)[0].lower()


def validate_team(team: object) -> list[dict[str,str]]:
    if not isinstance(team,list) or len(team)>MAX_HELPERS: raise InstallError("Choose up to fifty helper slots.")
    clean=[]
    for item in team:
        if not isinstance(item,dict) or set(item)-{"role","model","duty"}: raise InstallError("Invalid helper slot.")
        role=item.get("role"); model=item.get("model",""); duty=item.get("duty","")
        if role not in TEAM_ROLES or not isinstance(model,str) or (model and not SELECTOR.fullmatch(model)) or not isinstance(duty,str) or not DUTY.fullmatch(duty): raise InstallError("Invalid helper role, model, or duty.")
        clean.append({"role":role,"model":model,"duty":duty.strip()})
    return clean


def slot_name(index: int) -> str: return f"helper-{index:02d}"


def configured_slot(index: int, item: dict[str,str]) -> bytes:
    role=item["role"]
    base=(ROOT/f".opencode/agents/{role}.md").read_text(encoding="utf-8")
    if item["duty"]:
        base+=f"\nAssigned duty for this helper slot: {item['duty']}\n"
    return base.replace("description: ",f"description: Helper {index:02d} · ",1).encode("utf-8")


def configured_template(profile: str, default_model: str | None, role_models: dict[str,str], allow_mixed: bool, team: list[dict[str,str]] | None = None) -> bytes:
    team=validate_team(team or [])
    if profile != "custom" and (default_model or role_models) and not team:
        raise InstallError("Model selectors are available only with the custom profile unless a team is selected.")
    text = (ROOT / ".opencode/opencode.jsonc").read_text(encoding="utf-8")
    config = json.loads(text)
    steps = PROFILES["balanced"] if profile == "custom" else PROFILES[profile]
    for role in ROLES: config["agents"][role]["steps"] = steps[role]
    selectors = []
    if default_model:
        default_model = selector(default_model)
        if "#" in default_model: raise InstallError("Root model does not retain a #variant in OpenCode V2; use a role selector for variants.")
        config["model"] = default_model; selectors.append(default_model)
    for role, model in role_models.items():
        if role not in ROLES: raise InstallError(f"Unknown role in --role-model: {role}")
        model = selector(model); config["agents"][role]["model"] = model; selectors.append(model)
    if team:
        owner=config["agents"]["owner"]
        owner["permissions"]=[owner["permissions"][0],owner["permissions"][1],{"action":"subagent","resource":"acceptance-test-author","effect":"allow"},*[{"action":"subagent","resource":slot_name(index),"effect":"allow"} for index in range(1,len(team)+1)],*owner["permissions"][-2:]]
        for index,item in enumerate(team,1):
            slot=slot_name(index); role=item["role"]
            agent=copy.deepcopy(config["agents"][role]); agent["steps"]=steps[role]
            agent["description"]=f"Helper {index:02d}: {item['duty'] or role}"
            if item["model"]: agent["model"]=item["model"]; selectors.append(item["model"])
            config["agents"][slot]=agent
    if (role_models or any(item["model"] for item in team)) and not default_model and not allow_mixed:
        raise InstallError("Role-only model overrides have an unknown inherited default provider; supply --model or explicitly pass --allow-mixed-providers.")
    providers = {provider(item) for item in selectors}
    if len(providers) > 1 and not allow_mixed:
        raise InstallError("Mixed providers require --allow-mixed-providers and explicit user confirmation.")
    return (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def configured_agent(role: str, profile: str, role_models: dict[str,str], team: list[dict[str,str]] | None = None) -> bytes:
    # Runtime scalar settings live in JSON; Markdown holds role prompts/permissions.
    text=(ROOT/f".opencode/agents/{role}.md").read_text(encoding="utf-8")
    if role=="owner" and team:
        start=text.index("permissions:"); end=text.index("---",start)
        rules='permissions:\n  - { action: "*", resource: "*", effect: deny }\n  - { action: subagent, resource: "*", effect: deny }\n'
        rules+='  - { action: subagent, resource: acceptance-test-author, effect: allow }\n'
        rules+=''.join(f'  - {{ action: subagent, resource: {slot_name(index)}, effect: allow }}\n' for index in range(1,len(team)+1))
        rules+='  - { action: skill, resource: bounded-orchestrator, effect: allow }\n  - { action: question, resource: "*", effect: allow }\n'
        text=text[:start]+rules+text[end:]
    return text.encode("utf-8")


def assert_private_backups(target: Path) -> None:
    root=target/BACKUPS
    ensure_safe_parent(target,BACKUPS/"preflight"/"check")
    if root.is_symlink() or (root.exists() and not root.is_dir()): raise InstallError("Unsafe backup directory")
    if root.exists() and any(path.is_symlink() for path in root.rglob("*")):
        raise InstallError("Symlink inside private backups refused")


def backup(target: Path, relative: Path) -> Path:
    assert_private_backups(target)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = target / BACKUPS / stamp / relative
    suffix = 1
    while destination.exists() or destination.is_symlink():
        if destination.is_symlink(): raise InstallError("Symlinked backup destination refused")
        destination = destination.with_name(f"{destination.name}.{suffix}"); suffix += 1
    ensure_safe_parent(target,destination.relative_to(target))
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(target / relative, destination)
    try: os.chmod(destination,0o600)
    except OSError: pass
    return destination


def replace_block(existing: str, block: str) -> str:
    if START in existing or END in existing:
        if existing.count(START) != 1 or existing.count(END) != 1 or existing.index(START) > existing.index(END): raise InstallError("AGENTS.md has malformed managed markers")
        before = existing[:existing.index(START)].rstrip(); after = existing[existing.index(END)+len(END):].lstrip("\n")
        return ((before + "\n\n") if before else "") + block.strip() + "\n" + (("\n" + after) if after else "")
    return existing.rstrip() + ("\n\n" if existing.strip() else "") + block.strip() + "\n"


def original_agents_backup(target: Path, installed: bytes) -> bytes | None:
    """Recover exact pre-install formatting only when an untouched backup proves it."""
    template=(ROOT/'templates/AGENTS.block.md').read_text(encoding='utf-8')
    for path in sorted((target/BACKUPS).glob('*/AGENTS.md*')):
        if path.is_symlink() or not path.is_file(): continue
        original=path.read_bytes()
        if START.encode() in original or END.encode() in original: continue
        try: text=original.decode('utf-8').replace('\r\n','\n').replace('\r','\n')
        except UnicodeDecodeError: continue
        if replace_block(text,template).encode()==installed: return original
    return None


def preserve_project_settings(target, manifest, generated, profile, role_models, team, replace, default_model):
    """Patch managed fields during updates, retaining unrelated JSONC and preferences."""
    if '.opencode/opencode.jsonc' not in manifest['files']:
        return generated
    config_path = target / '.opencode/opencode.jsonc'
    ensure_safe_parent(target, Path('.opencode/opencode.jsonc'))
    if not config_path.exists():
        return generated
    tools = str(ROOT / '.opencode/tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import console
    try:
        text, current = console.read(target / '.opencode/opencode.jsonc')
        if digest(target / '.opencode/opencode.jsonc') != manifest['files']['.opencode/opencode.jsonc']['sha256'] and not replace:
            raise InstallError('Project settings changed elsewhere; confirm backup and replace.')
        if not isinstance(current.get('agents', {}), dict):
            raise InstallError('Existing agent settings require manual repair.')
        desired = json.loads(generated)
        updated = text
        for key, value in desired.items():
            if key != 'agents':
                updated = console.patch(updated, [key], value)
        # CLI omission retains the saved chief; the browser sends an explicit empty choice.
        if default_model == '':
            updated = console.patch(updated, ['model'], None, True)
        for role, values in desired['agents'].items():
            old_team = manifest.get('team', [])
            index = int(role[-2:]) - 1 if role.startswith('helper-') else None
            same_role = index is None or (index < len(old_team) and old_team[index].get('role') == team[index]['role'])
            previous = current.get('agents', {}).get(role, {})
            if not isinstance(previous, dict):
                raise InstallError('Existing agent settings require manual repair.')
            for key, value in values.items():
                if key == 'steps' and profile == 'custom' and same_role and key in previous:
                    continue
                updated = console.patch(updated, ['agents', role, key], value)
            # Base role models are kept unless explicitly replaced.
            if role.startswith('helper-'):
                base = current.get('agents', {}).get(team[index]['role'], {})
                if not team[index]['model']:
                    inherited = role_models.get(team[index]['role'], base.get('model') if isinstance(base, dict) else None)
                    updated = console.patch(updated, ['agents', role, 'model'], inherited, inherited is None)
                if profile == 'custom' and not (same_role and 'steps' in previous) and isinstance(base, dict) and 'steps' in base:
                    updated = console.patch(updated, ['agents', role, 'steps'], base['steps'])
        for name in manifest['files']:
            if SLOT.fullmatch(name) and Path(name).stem not in desired['agents']:
                updated = console.patch(updated, ['agents', Path(name).stem], None, True)
        json.loads(console.scrub(updated))
        return updated.encode('utf-8')
    except console.ConsoleError as exc:
        raise InstallError(str(exc)) from exc


def install(target: Path, profile: str, replace: bool, dry_run: bool, default_model: str | None, role_models: dict[str,str], allow_mixed: bool, team: list[dict[str,str]] | None = None, on_commit=None) -> list[str]:
    if not target.is_dir() or target.is_symlink(): raise InstallError("Target must be an existing real directory")
    manifest = load_manifest(target); actions=[]; files=dict(manifest["files"])
    team=validate_team(manifest.get("team",[]) if team is None else team)
    config_data = configured_template(profile, default_model, role_models, allow_mixed, team)
    config_data = preserve_project_settings(target, manifest, config_data, profile, role_models, team, replace, default_model)
    agents = target / "AGENTS.md"
    if agents.is_symlink() or (agents.exists() and not agents.is_file()): raise InstallError("Refusing unsafe AGENTS.md path")
    block=(ROOT/"templates/AGENTS.block.md").read_text(encoding="utf-8")
    existing=agents.read_text(encoding="utf-8") if agents.exists() else ""; updated=replace_block(existing, block)
    sentinel_relative=Path('.opencode/.bounded-orchestrator/.gitignore');sentinel=target/sentinel_relative
    ensure_safe_parent(target,sentinel_relative)
    if sentinel.exists() and (sentinel.is_symlink() or not sentinel.is_file() or sentinel.read_text(encoding='utf-8')!='*\n!.gitignore\n'):
        raise InstallError('Private runtime ignore sentinel changed; refusing backup or install.')
    assert_private_backups(target)
    dynamic=[Path(f".opencode/agents/{slot_name(index)}.md") for index in range(1,len(team)+1)]
    removals=[]; writes=[]
    # Plan and preflight every dynamic and managed path before touching any file.
    for name in list(files):
        if SLOT.fullmatch(name) and Path(name) not in dynamic:
            relative=Path(name);ensure_safe_parent(target,relative);destination=target/relative
            if destination.is_symlink() or (destination.exists() and (not destination.is_file() or digest(destination)!=files[name]['sha256'])):
                raise InstallError(f"Modified old helper slot must be resolved before replacement: {name}")
            removals.append((relative,destination));files.pop(name);actions.append(f"REMOVE {relative}")
    for relative in [*MANAGED,*dynamic]:
        ensure_safe_parent(target,relative)
        destination=target/relative
        if relative==Path('.opencode/tools/work_protocol'): data=work_protocol_wrapper(target).encode()
        elif relative==Path('.opencode/opencode.jsonc'): data=config_data
        elif relative==sentinel_relative: data=b'*\n!.gitignore\n'  # Runtime sentinel bytes must be canonical even after a CRLF checkout.
        elif relative in dynamic: data=configured_slot(int(relative.stem[-2:]),team[int(relative.stem[-2:])-1])
        elif relative.parent==Path('.opencode/agents'): data=configured_agent(relative.stem,profile,role_models,team)
        else: data=(ROOT/relative).read_bytes()
        wanted=hashlib.sha256(data).hexdigest()
        if destination.is_symlink() or (destination.exists() and not destination.is_file()): raise InstallError(f"Refusing unsafe path: {relative}")
        current=digest(destination) if destination.exists() else None
        if current is not None and current!=wanted:
            old_owned=files.get(relative.as_posix(),{}).get('sha256')==current
            if not old_owned and not replace:
                if team: raise InstallError(f'Conflicting team file must be resolved before install: {relative}')
                actions.append(f"KEEP {relative} (conflict)");continue
            actions.append(f"BACKUP {relative}")
        if current!=wanted:
            writes.append((relative,data,current is not None));actions.append(f"INSTALL {relative}")
        else: actions.append(f"UNCHANGED {relative}")
        files[relative.as_posix()]={'sha256':wanted}
    if updated!=existing: actions.append('UPDATE AGENTS.md managed block')
    if dry_run: return actions
    # Every active path has passed validation. Retain its original bytes until the
    # manifest is committed so a recoverable mid-write failure cannot leave a
    # partially installed team. Private backup copies remain protected on failure.
    touched=[*[path for _,path in removals],*[target/relative for relative,_,_ in writes],agents,target/MANIFEST]
    before={path:path.read_bytes() if path.exists() else None for path in dict.fromkeys(touched)}
    mutated={}
    def unchanged(path: Path) -> None:
        ensure_safe_parent(target,path.relative_to(target))
        if path.is_symlink() or (path.exists() and not path.is_file()): raise InstallError(f'Path changed during install: {path.relative_to(target)}')
        current=path.read_bytes() if path.exists() else None
        if current!=before[path]: raise InstallError(f'Path changed during install: {path.relative_to(target)}')
    try:
        if not sentinel.exists():
            sentinel.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            atomic_bytes(sentinel,b'*\n!.gitignore\n')
            try: os.chmod(sentinel.parent,0o700)
            except OSError: pass
        for relative,destination in removals:
            unchanged(destination)
            if destination.exists():backup(target,relative);destination.unlink();mutated[destination]=None
        for relative,data,needs_backup in writes:
            if relative==sentinel_relative and before[target/relative] is None and sentinel.is_file() and sentinel.read_bytes()==data:
                continue  # Our private ignore sentinel was created just above; keep it on rollback.
            unchanged(target/relative)
            if needs_backup:backup(target,relative)
            atomic_bytes(target/relative,data);mutated[target/relative]=data
        if updated!=existing:
            unchanged(agents)
            if agents.exists():backup(target,Path('AGENTS.md'))
            atomic_bytes(agents,updated.encode());mutated[agents]=updated.encode()
        payload={'schema':1,'version':(ROOT/'VERSION').read_text().strip(),'profile':profile,'files':files,'agents_block':True,'agents_block_sha256':hashlib.sha256(block.strip().encode()).hexdigest(),'team':team}
        unchanged(target/MANIFEST)
        manifest_data=(json.dumps(payload,indent=2,sort_keys=True)+'\n').encode()
        atomic_bytes(target/MANIFEST,manifest_data);mutated[target/MANIFEST]=manifest_data
        if on_commit is not None: on_commit()
    except Exception as exc:
        failed=[]
        for path,applied in reversed(list(mutated.items())):
            try:
                ensure_safe_parent(target,path.relative_to(target))
                if path.is_symlink(): raise InstallError('Rollback path became a symlink.')
                current=path.read_bytes() if path.exists() else None
                if current!=applied: raise InstallError('Rollback path changed outside this install.')
                data=before[path]
                if data is None:
                    if path.exists(): path.unlink()
                elif not path.exists() or path.read_bytes()!=data: atomic_bytes(path,data)
            except Exception as rollback_exc: failed.append(f'{path.relative_to(target)}: {rollback_exc}')
        if failed: raise InstallError('Install failed; rollback incomplete: '+', '.join(failed)) from exc
        raise
    return actions


def uninstall(target: Path, dry_run: bool, expected_actions: list[str] | None = None) -> list[str]:
    manifest_path=target/MANIFEST
    ensure_safe_parent(target,MANIFEST)
    if manifest_path.is_symlink() or (manifest_path.exists() and not manifest_path.is_file()): raise InstallError('Refusing unsafe install manifest')
    manifest_before=manifest_path.read_bytes() if manifest_path.is_file() else None
    manifest=load_manifest(target);actions=[];planned=[]
    if (manifest_path.read_bytes() if manifest_path.is_file() else None)!=manifest_before:
        raise InstallError('Install manifest changed during uninstall')
    assert_private_backups(target)
    agents=target/'AGENTS.md'
    if agents.is_symlink() or (agents.exists() and not agents.is_file()): raise InstallError('Refusing unsafe AGENTS.md path')
    for name,meta in manifest['files'].items():
        relative=Path(name);path=target/relative;ensure_safe_parent(target,relative)
        if path.is_symlink() or (path.exists() and not path.is_file()): raise InstallError(f'Refusing unsafe path: {relative}')
        if relative in IGNORE_SENTINELS:
            planned.append(('sentinel',relative,path));actions.append(f'KEEP {relative} (runtime ignore sentinel)');continue
        if path.is_file() and digest(path)==meta.get('sha256'):
            planned.append(('remove',relative,path));actions.append(f'REMOVE {relative}')
        elif path.exists():actions.append(f'KEEP {relative} (modified)')
    cleaned=None;agents_before=None
    if manifest.get('agents_block') and agents.is_file():
        agents_before=agents.read_bytes()
        start=START.encode();end=END.encode()
        if start in agents_before or end in agents_before:
            if agents_before.count(start)!=1 or agents_before.count(end)!=1 or agents_before.index(start)>agents_before.index(end):
                actions.append('KEEP AGENTS.md managed block (markers changed)')
            else:
                begin=agents_before.index(start);finish=agents_before.index(end)+len(end)
                block=agents_before[begin:finish]
                expected=manifest.get('agents_block_sha256') or hashlib.sha256((ROOT/'templates/AGENTS.block.md').read_text(encoding='utf-8').strip().encode()).hexdigest()
                if hashlib.sha256(block.replace(b'\r\n',b'\n')).hexdigest()==expected:
                    cleaned=original_agents_backup(target,agents_before)
                    if cleaned is None: cleaned=agents_before[:begin]+agents_before[finish:]
                    actions.append('REMOVE AGENTS.md managed block')
                else: actions.append('KEEP AGENTS.md managed block (modified)')
    if expected_actions is not None and actions!=expected_actions:
        raise InstallError('Uninstall plan changed; review again.')
    if dry_run:return actions
    if manifest_before is None:return actions
    if os.name!='nt':
        _uninstall_staged(target,manifest,planned,agents_before,cleaned,manifest_before)
        return actions
    before={}
    for kind,relative,path in planned:
        if kind=='remove':
            ensure_safe_parent(target,relative)
            if path.is_symlink() or not path.is_file(): raise InstallError(f'Path changed during uninstall: {relative}')
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=manifest['files'][relative.as_posix()]['sha256']:
                raise InstallError(f'Path changed during uninstall: {relative}')
            before[path]=data
    mutated={}
    try:
        for kind,relative,path in planned:
            if kind=='remove':
                ensure_safe_parent(target,relative)
                if path.is_symlink() or not path.is_file() or path.read_bytes()!=before[path]:
                    raise InstallError(f'Path changed during uninstall: {relative}')
                path.unlink();mutated[path]=None
            elif not path.exists():atomic_bytes(path,(ROOT/relative).read_bytes())
        if cleaned is not None:
            if agents.is_symlink() or not agents.is_file() or agents.read_bytes()!=agents_before:
                raise InstallError('AGENTS.md changed during uninstall')
            atomic_bytes(agents,cleaned);mutated[agents]=cleaned
        if manifest_path.exists():
            if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.read_bytes()!=manifest_before:
                raise InstallError('Install manifest changed during uninstall')
            manifest_path.unlink();mutated[manifest_path]=None
    except Exception as exc:
        failed=[]
        for path,applied in reversed(list(mutated.items())):
            try:
                ensure_safe_parent(target,path.relative_to(target))
                if path.is_symlink() or (path.exists() and not path.is_file()): raise InstallError('Rollback path changed.')
                current=path.read_bytes() if path.exists() else None
                if current!=applied: raise InstallError('Rollback path changed outside this uninstall.')
                original=agents_before if path==agents else manifest_before if path==manifest_path else before[path]
                atomic_bytes(path,original)
            except Exception as rollback_exc: failed.append(f'{path.relative_to(target)}: {rollback_exc}')
        if failed: raise InstallError('Uninstall failed; rollback incomplete: '+', '.join(failed)) from exc
        raise
    return actions


def parse_roles(values: list[str]) -> dict[str,str]:
    result={}
    for item in values:
        if "=" not in item: raise InstallError("--role-model requires role=provider/model[#variant]")
        role, model=item.split("=",1)
        if role in result: raise InstallError(f"Duplicate role model: {role}")
        result[role]=model
    return result


def guided(args: argparse.Namespace) -> None:
    if args.target is None: args.target=Path(input("Drag the target repository folder here / Hedef klasörü sürükleyin:\n> ").strip().strip("'\""))
    if args.action is None:
        choice=input("\n[ ACTION / İŞLEM ]\n1) Safe install/update\n2) Dry run only\n3) Uninstall\nSelect [1]: ").strip() or "1"
        args.action={"1":"install","2":"dry-run","3":"uninstall"}.get(choice,"install")
    if args.action in {"install","dry-run"} and args.profile is None:
        choice=input("\n[ PROFILE / PROFİL ]\n1) Balanced / Dengeli\n2) Quality / Yüksek kalite\n3) Economy / Ekonomik\n4) Quota saver / Kota tasarrufu\n5) Custom / Özel\nSelect [1]: ").strip() or "1"
        args.profile={"1":"balanced","2":"quality","3":"economy","4":"quota-saver","5":"custom"}.get(choice,"balanced")
    if args.profile=="custom" and args.model is None:
        value=input("Default model selector (provider/model[#variant], blank=inherited): ").strip()
        args.model=value or None
        values=input("Optional role selectors, comma-separated role=provider/model[#variant] (blank=none): ").strip()
        if values: args.role_model.extend(item.strip() for item in values.split(",") if item.strip())
        selected=[args.model] if args.model else []
        selected.extend(item.split("=",1)[1] for item in args.role_model if "=" in item)
        if args.role_model and not args.model:
            print("Role overrides may differ from the provider inherited by the current OpenCode session.")
            args.allow_mixed_providers=(input("Allow role override with unknown inherited provider? Type MIX to confirm: ").strip()=="MIX")
        elif len({provider(item) for item in selected}) > 1:
            args.allow_mixed_providers=(input("Mix providers across native roles? Type MIX to confirm: ").strip()=="MIX")
    if args.action=="install" and not args.replace:
        args.replace=(input("Back up and replace conflicting managed files? [y/N]: ").strip().lower()=="y")


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target",type=Path); parser.add_argument("--action",choices=("install","dry-run","uninstall"))
    parser.add_argument("--profile",choices=(*PROFILES,"custom")); parser.add_argument("--replace",action="store_true")
    parser.add_argument("--model"); parser.add_argument("--role-model",action="append",default=[]); parser.add_argument("--allow-mixed-providers",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.target is None or args.action is None or (args.action in {"install","dry-run"} and args.profile is None): guided(args)
        target=args.target.expanduser().resolve(); profile=args.profile or "balanced"; dry=args.action=="dry-run"
        if args.action=="uninstall": actions=uninstall(target,dry)
        else: actions=install(target,profile,args.replace,dry,args.model,parse_roles(args.role_model),args.allow_mixed_providers)
    except (InstallError,OSError,EOFError) as exc: print(f"INSTALL ERROR: {exc}",file=sys.stderr); return 2
    print("\n".join(actions)); print(f"\nTarget: {target}\nProfile: {profile}" + ("\nDRY RUN: no files changed" if dry else "")); return 0


if __name__=="__main__": raise SystemExit(main())
