"""Offline Antigravity project settings; no prompts, auth, global writes or model calls.

Transactions touch a finite provider namespace, preserve unrelated files, refuse
symlinks and changed owned files, and retain durable before/after backup receipts.
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import shlex
import shutil
import subprocess
import tempfile
import time
import install

META = '.antigravity/.bounded-orchestrator'
MANIFEST = META + '/install.json'
MAX_FILE = 2 * 1024 * 1024
CAPABILITIES = {'preview': True, 'apply': True, 'restore': True, 'jobs': False, 'usage': False, 'role_effort': False, 'native_concurrency_limit': False, 'max_concurrency': 1}
LIMITATIONS = [
    'One active helper is Ustam policy, not a verified native concurrency limit.',
    'Agent roles and provider routing are prompt conventions; native read-only chief enforcement is unverified.',
    'Legacy IDE custom agents are unverified; this installation requires the supported agy CLI.',
    'Model tiers do not verify account access. Per-role effort is unsupported.',
    'Unattended Jobs and project-scoped usage collection are unsupported.',
]
POLICY = '''Use this policy only in Antigravity with the ustam-antigravity-owner agent.
Other providers keep their own routing. Codex-specific model, skill and tool routing
belongs to Codex; preserve shared repository invariants and user instructions.
Use only this provider's namespaced agents and skill. Never invoke another
provider's owner. These routing instructions are prompt conventions, not a sandbox.
The owner only scopes, delegates, reads short evidence and speaks with the user.
Helpers never delegate. One writer owns each file; at most one active helper.
Use independent acceptance-test-author before implementation, isolated prepare,
candidate freeze, verifier checks, read-only review, integration recheck and explicit
human apply through Ustam Works. At most two implementation and verification
attempts and one repair/re-review cycle; stop on missing authority or capabilities.
Do not install dependencies, alter permissions, commit, push, publish or deploy.
Installation does not start a job or authorize a model call.
'''
SKILL = '''---
name: ustam-antigravity-orchestrator
description: Bounded one-level orchestration for the Antigravity Ustam owner only.
---
# Antigravity bounded project orchestration
''' + POLICY + '''
## Manual local Works bridge
Task text is written once in native chat. Delegate protocol publication to a bounded
helper with explicit scope, criteria, owned relative files and exact command argv.
Use the installed .antigravity/tools/work_protocol.py bridge with the installer
Python runtime with -I (or the POSIX work_protocol wrapper); it captures the trusted Ustam
runtime, provider and project. It never accepts a native apply/approve/answer action.
Publish create and an independent acceptance-test-author test_pack before prepare.
Implement only in the detached workspace returned by prepare. Freeze the candidate;
verifier check creates controller-observed receipts; reported_check is self-report.
Run integration_check on the combined current tree before human apply in Ustam.
Publish question when scope/permission changes; only the trusted Ustam UI can answer,
approve or apply. Cancel/resume retain work. Operation IDs are unique and cannot be
reused with different input. Never reset or overwrite a user edit. Stop if the bridge,
Git, shell permission or a check kind is missing; never invent a passing receipt.
Use invoke_subagent according to its actual runtime tool schema; do not invent
parameter names. Native temporary worktrees may be removed; keep candidates in the
workspace controlled by Ustam prepare. Worktrees are not security sandboxes.
Read-only specialists never modify production. Verifier runs only approved local
checks and never repairs. Test author writes only the owned tests/pack. Implementer
is the sole production writer. Reviewer returns evidence-backed findings only.
'''


class TransactionError(ValueError):
    def __init__(self, message, stage, recovery_status):
        super().__init__(message)
        self.stage, self.recovery_status = stage, recovery_status


_ACTIVE_ROOT = ContextVar('antigravity_transaction_root', default=None)


def identity(root):
    info = root.stat(follow_symlinks=False)
    if root.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_ino <= 0 or info.st_dev < 0:
        raise ValueError('Stable project directory identity unavailable')
    return {'device': info.st_dev, 'inode': info.st_ino}


def bound(method):
    @wraps(method)
    def checked(self, *args, **kwargs):
        if identity(self.target) != self.root_identity:
            raise ValueError('Project directory identity changed; no mutation permitted')
        token = _ACTIVE_ROOT.set((self.target, self.root_identity))
        try: return method(self, *args, **kwargs)
        finally: _ACTIVE_ROOT.reset(token)
    return checked


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def allowed(name):
    return name in {MANIFEST, '.agents/rules/ustam-antigravity.md', '.agents/skills/ustam-antigravity-orchestrator/SKILL.md', '.antigravity/tools/work_protocol.py', '.antigravity/tools/work_protocol'} or bool(re.fullmatch(r'\.agents/agents/ustam-antigravity-(owner|helper-(0[1-9]|[1-4][0-9]|50))\.md', name))


def safe(root, name):
    active = _ACTIVE_ROOT.get()
    if active and active[0] == root and identity(root) != active[1]:
        raise ValueError('Project directory identity changed; no mutation permitted')
    if root.is_symlink() or root.resolve(strict=True) != root:
        raise ValueError('Project path changed; refresh before installing')
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError('Unsafe project path')
    current = root
    for part in p.parts:
        current = current / part
        if current.is_symlink() or (current.exists() and getattr(current.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise ValueError('Symlink or reparse point in provider path refused: ' + name)
        if not current.resolve().is_relative_to(root):
            raise ValueError('Provider path escaped the registered project: ' + name)
        if current != root / p and current.exists() and not current.is_dir():
            raise ValueError('Provider parent is not a directory: ' + name)
    if current.exists() and (not current.is_file() or current.stat().st_size > MAX_FILE):
        raise ValueError('Provider file type/size refused: ' + name)
    return current


@contextmanager
def parent_fd(root, name, create=False):
    """Walk canonical ancestors without following symlinks (POSIX)."""
    safe(root, name)
    parts = [*root.parts[1:], *Path(name).parts[:-1]]
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(parts):
            if create and i >= len(root.parts) - 1:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
            active = _ACTIVE_ROOT.get()
            if i == len(root.parts) - 2 and active and active[0] == root:
                info = os.fstat(fd)
                if {'device': info.st_dev, 'inode': info.st_ino} != active[1]:
                    raise ValueError('Project directory identity changed during traversal')
        safe(root, name)
        actual, opened = (root / name).parent.stat(), os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('Provider directory changed during transaction')
        yield fd
    finally:
        os.close(fd)


def read_at(fd, leaf):
    try: file_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError: return None
    with os.fdopen(file_fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise ValueError('Provider file type/size refused')
        data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE: raise ValueError('Provider file exceeds bounds')
        return data


def read(root, name):
    path = safe(root, name)
    if os.name == 'nt':
        return path.read_bytes() if path.exists() else None
    try:
        with parent_fd(root, name) as fd: return read_at(fd, path.name)
    except FileNotFoundError: return None


def write(root, name, data, expected):
    path = safe(root, name)
    if os.name != 'nt':
        with parent_fd(root, name, create=True) as directory:
            if read_at(directory, path.name) != expected:
                raise ValueError('Concurrent edit refused: ' + name)
            if data is None:
                try: os.unlink(path.name, dir_fd=directory)
                except FileNotFoundError: pass
                os.fsync(directory)
                return
            temporary = '.ustam-antigravity-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755 if name.endswith('/work_protocol') else 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as out:
                    out.write(data); out.flush(); os.fsync(out.fileno())
                safe(root, name)
                if read_at(directory, path.name) != expected:
                    raise ValueError('Concurrent edit refused: ' + name)
                os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try: os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError: pass
        return
    # Windows uses the same expected-byte transaction and reparse/symlink checks.
    if read(root, name) != expected:
        raise ValueError('Concurrent edit refused: ' + name)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe(root, name)
    if data is None:
        path.unlink(missing_ok=True)
        return
    fd, tmp = tempfile.mkstemp(prefix='.ustam-antigravity-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        safe(root, name)
        if read(root, name) != expected:
            raise ValueError('Concurrent edit refused: ' + name)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def rollback(root, attempted, original, desired):
    """Observe actual post-write state, including failures after replace/unlink."""
    for name in reversed(attempted):
        try:
            live = read(root, name)
            if live == original[name]: continue
            if live == desired[name]: write(root, name, original[name], desired[name])
        except Exception:
            # A rollback write can itself fail after mutation. The final byte
            # comparison below decides recovery status, not the exception alone.
            pass
    conflicts = []
    for name, expected in original.items():
        try:
            if read(root, name) != expected: conflicts.append(name)
        except Exception: conflicts.append(name)
    return conflicts


@contextmanager
def locked(root):
    name = META + '/install.lock'
    if os.name != 'nt':
        import fcntl
        with parent_fd(root, name, create=True) as directory:
            fd = os.open('install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('Unsafe installer lock')
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
        return
    import msvcrt
    path = safe(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe(root, name)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size == 0: os.write(fd, b'0')
        os.lseek(fd, 0, os.SEEK_SET); msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        yield
    finally:
        os.lseek(fd, 0, os.SEEK_SET); msvcrt.locking(fd, msvcrt.LK_UNLCK, 1); os.close(fd)


def probe(root):
    # Worker PATH is constructed only from safe external CLI directories by Ustam.
    executable = shutil.which('agy')
    reason, version = 'Install the Antigravity agy CLI 1.2.16 or newer separately; Ustam does not install it.', None
    status = 'missing'
    if executable:
        path = Path(executable).resolve()
        if path.is_relative_to(root) or (os.name == 'nt' and path.suffix.lower() != '.exe'):
            return {'status': 'unsupported', 'version': None, 'reason': 'Project-local or batch CLI refused.'}
        try:
            v = subprocess.run([str(path), '--version'], cwd=tempfile.gettempdir(), capture_output=True, timeout=5)
            text = v.stdout.decode('utf-8', 'replace').strip()
            match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', text)
            if not v.returncode and match:
                version = text.lstrip('v')
                if tuple(map(int, match.groups())) < (1, 2, 16):
                    reason = 'Antigravity CLI below 1.2.16 is unsupported; update it separately.'
                else:
                    h = subprocess.run([str(path), '--help'], cwd=tempfile.gettempdir(), capture_output=True, timeout=5)
                    help_bytes = h.stdout + h.stderr
                    help_text = help_bytes[:65536].decode('utf-8', 'replace')
                    if h.returncode == 0 and len(help_bytes) <= 65536 and all(flag in help_text for flag in ('--agent', '--model', '--output-format', 'agents')):
                        return {'status': 'supported', 'version': version, 'reason': 'Local version/help capabilities verified; account access and native execution are unverified.'}
                    reason = 'Installed agy lacks the required local agent/model capabilities.'
            else:
                reason = 'Unknown Antigravity CLI version; a verified version and local capability probe are required.'
            status = 'unsupported'
        except (OSError, subprocess.TimeoutExpired):
            status, reason = 'unsupported', 'Antigravity CLI capability probe failed; inspect its local installation.'
    return {'status': status, 'version': version, 'reason': reason}


def validate(payload):
    keys = {'id', 'name', 'task_type', 'version', 'schema', 'provider', 'chief', 'helpers', 'concurrency', 'profile', 'replace', 'allow_mixed'}
    if not isinstance(payload, dict) or set(payload) - keys or payload.get('provider', 'antigravity') != 'antigravity' or payload.get('version', payload.get('schema', 1)) != 1:
        raise ValueError('Invalid Antigravity orchestra')
    if type(payload.get('concurrency', 1)) is not int or payload.get('concurrency', 1) != 1:
        raise ValueError('Antigravity supports one active helper by Ustam policy only')
    if payload.get('profile', 'balanced') not in install.PROFILES:
        raise ValueError('Unsupported Antigravity profile')
    chief, helpers = payload.get('chief'), payload.get('helpers')
    if not isinstance(chief, dict) or set(chief) != {'model', 'effort'} or chief.get('model') not in install.MODELS or chief.get('effort') != '':
        raise ValueError('Antigravity chief requires model inherit/flash/pro and empty effort')
    if not isinstance(helpers, list) or not 1 <= len(helpers) <= 50:
        raise ValueError('Select 1 to 50 Antigravity helpers')
    for h in helpers:
        if not isinstance(h, dict) or set(h) != {'id', 'role', 'name', 'model', 'effort'} or not all(isinstance(v, str) for v in h.values()) or not h['id'] or len(h['id']) > 100 or not 1 <= len(h['name']) <= 48 or h['role'] not in install.ROLES or h['model'] not in install.MODELS or h['effort'] != '':
            raise ValueError('Unsupported Antigravity helper role/model/effort')
    if len({h['id'] for h in helpers}) != len(helpers):
        raise ValueError('Duplicate helper identity')
    return json.loads(json.dumps(payload))


def agent(name, role, model, owner=False, label=None):
    tools = ['invoke_subagent'] if owner else ['view_file', 'grep_search']
    if role in ('implementer', 'acceptance-test-author'):
        tools += ['replace_file_content', 'write_to_file', 'run_command']
    elif role in ('verifier', 'qa-operator'):
        tools += ['run_command']
    prompt = POLICY if owner else ('You are the bounded ' + role + '. Never delegate. Execute only the assigned ownership and acceptance contract.\n' +
        ('Write only the assigned production files in the Ustam prepared workspace.\n' if role == 'implementer' else
         'Write only the assigned acceptance tests and publish the test pack before implementation.\n' if role == 'acceptance-test-author' else
         'Never repair production code. Run only explicitly approved checks through the local bridge.\n' if role in ('verifier', 'qa-operator') else
         'Read only; do not run commands or modify files. Return evidence-backed findings.\n'))
    fields = {'name': name, 'description': 'Ustam Antigravity ' + role + ' (' + (label or role) + '); bounded explicit task only.', 'tools': tools, 'mainAgent': owner, 'subagent': not owner, 'model': model, 'commandExecutionPolicy': 'off'}
    # JSON values are valid YAML scalars/arrays; labels cannot inject frontmatter.
    return ('---\n' + '\n'.join(key + ': ' + json.dumps(value) for key, value in fields.items()) + '\n---\n\n' + prompt).encode()


class Settings:
    def __init__(self, target, bridge_command):
        self.target = Path(target).resolve(strict=True)
        if not self.target.is_dir(): raise ValueError('Project directory required')
        self.root_identity = identity(self.target)
        if not bridge_command or not all(isinstance(x, str) for x in bridge_command): raise ValueError('Trusted bridge runtime required')
        self.bridge_command = [*bridge_command, '--project', str(self.target)]
        self.pending = None

    @bound
    def manifest(self, verify=True):
        raw = read(self.target, MANIFEST)
        if raw is None: return None
        m = json.loads(raw)
        if not isinstance(m, dict) or m.get('schema') != 1 or m.get('provider') != 'antigravity' or not isinstance(m.get('files'), dict) or not m['files'] or len(m['files']) > 55 or not re.fullmatch(r'[a-f0-9]{32}', m.get('backup_id', '')):
            raise ValueError('Invalid Antigravity install metadata')
        if m.get('root_identity') != self.root_identity:
            raise ValueError('Installed project directory identity changed; copied installations cannot be restored or overwritten')
        validate(m.get('orchestra'))
        for name, entry in m['files'].items():
            if name == MANIFEST or not allowed(name) or not isinstance(entry, dict) or entry.get('owned') is not True or not re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256', '')):
                raise ValueError('Invalid owned source manifest')
            if verify and digest(read(self.target, name)) != entry['sha256']:
                raise ValueError('Installed Antigravity files changed or incomplete; preserve user edits and inspect recovery')
        return m

    @bound
    def support(self):
        cli = probe(self.target)
        return {'cli': cli, 'legacy_ide': {'status': 'unverified', 'reason': 'IDE installation does not verify custom agent support.'},
                'native_agents': {'status': 'local_help_verified' if cli['status'] == 'supported' else 'unsupported'},
                'jobs': {'status': 'unsupported'}, 'usage': {'status': 'unsupported'}}

    @bound
    def inspect(self):
        try:
            manifest = self.manifest()
            limitation = []
        except (ValueError, OSError) as exc:
            manifest, limitation = None, [str(exc)]
        team = manifest['orchestra'] if manifest else {}
        return {'provider': 'antigravity', 'target': str(self.target), 'installed': bool(manifest), 'chief': team.get('chief', {'model': 'inherit', 'effort': ''}),
                'helpers': team.get('helpers', []), 'concurrency': 1, 'profile': team.get('profile', 'balanced'), 'restore_available': bool(manifest),
                'capabilities': dict(CAPABILITIES), 'support_matrix': self.support(), 'limitations': LIMITATIONS + limitation}

    @bound
    def models(self):
        return {'provider': 'antigravity', 'status': 'unverified', 'source': 'documented native agent model tiers',
                'models': [{'id': m, 'efforts': [], 'origin': 'documented_tier'} for m in install.MODELS], 'roles': list(install.ROLES),
                'efforts': [], 'chief_efforts': [], 'max_concurrency': 1, 'profiles': list(install.PROFILES), 'capabilities': dict(CAPABILITIES),
                'effort_semantics': 'unsupported', 'limitations': list(LIMITATIONS), 'support_matrix': self.support(),
                'profile_recommendations': {p: {'chief': {'model': values['owner'], 'effort': ''}, 'role_models': {r: values['helper'] for r in install.ROLES}, 'role_efforts': {}, 'concurrency': 1, 'source': 'documented native tiers; policy recommendations', 'access_verified': False} for p, values in install.PRESETS.items()}}

    def assets(self, team):
        values = {'.agents/rules/ustam-antigravity.md': ('---\ntrigger: always_on\ndescription: Ustam routing only for the Antigravity owner.\n---\n\n' + POLICY).encode(),
                  '.agents/skills/ustam-antigravity-orchestrator/SKILL.md': SKILL.encode(),
                  '.agents/agents/ustam-antigravity-owner.md': agent('ustam-antigravity-owner', 'owner', team['chief']['model'], True)}
        values['.agents/skills/ustam-antigravity-orchestrator/SKILL.md'] += ('\nTrusted bridge argv for this installation (append ACTION and request flags):\n```json\n' + json.dumps(self.bridge_command) + '\n```\nNative agent control names/arguments must follow the actual runtime tool schema.\n').encode()
        for i, h in enumerate(team['helpers'], 1):
            name = f'ustam-antigravity-helper-{i:02d}'
            values['.agents/agents/' + name + '.md'] = agent(name, h['role'], h['model'], label=h['name'])
        isolated = 'import subprocess,sys; raise SystemExit(subprocess.call(' + repr(self.bridge_command) + ' + sys.argv[1:]))'
        code = '#!/usr/bin/env python3\n# Fixed installer runtime/project; isolated stdlib imports.\nimport sys\nif not sys.flags.isolated: raise SystemExit("Run this bridge with Python -I or the fixed POSIX wrapper.")\n' + isolated + '\n'
        values['.antigravity/tools/work_protocol.py'] = code.encode()
        values['.antigravity/tools/work_protocol'] = ('#!/bin/sh\nexec ' + ' '.join(shlex.quote(a) for a in self.bridge_command) + ' "$@"\n').encode()
        return values

    @bound
    def plan(self, payload):
        team = validate(payload)
        support = self.support()
        if support['cli']['status'] != 'supported': raise ValueError(support['cli']['reason'])
        old = self.manifest()
        desired = self.assets(team)
        names = set(desired) | (set(old['files']) if old else set()) | {MANIFEST}
        before = {name: read(self.target, name) for name in names}
        for name in desired:
            if before[name] is not None and (not old or name not in old['files']):
                raise ValueError('Unowned Antigravity namespace collision refused: ' + name)
        # Snapshot every declared file and manifest; unrelated provider files never enter it.
        return team, old, desired, before, support

    @bound
    def preview(self, payload):
        team, old, desired, before, support = self.plan(payload)
        token = secrets.token_urlsafe(24)
        self.pending = (token, time.monotonic() + 300, team, before, dict(self.root_identity))
        changes = [{'path': n, 'action': 'delete' if n not in desired else 'create' if before[n] is None else 'update' if before[n] != desired[n] else 'unchanged'} for n in sorted(set(before) - {MANIFEST})]
        return {'provider': 'antigravity', 'target': str(self.target), 'preview_id': token, 'root_identity': dict(self.root_identity), 'files': changes, 'changes': changes,
                'support_matrix': support, 'capabilities': dict(CAPABILITIES), 'limitations': list(LIMITATIONS), 'phases': [{'phase': 'preview', 'status': 'completed'}]}

    @bound
    def apply(self, params):
        pending, self.pending = self.pending, None
        if set(params) != {'preview_id'} or not pending or pending[0] != params['preview_id'] or time.monotonic() > pending[1]: raise ValueError('Fresh Antigravity preview required')
        if len(pending) != 5 or pending[4] != self.root_identity:
            raise ValueError('Preview project directory identity changed; no mutation permitted')
        # Preflight runs before lock-directory creation and again while holding the lock.
        self.plan(pending[2])
        with locked(self.target):
            team, old, desired, before, support = self.plan(pending[2])
            if before != pending[3]: raise ValueError('Project changed since preview; preview again')
            phases = [{'phase': 'preflight', 'status': 'completed'}]
            stamp = datetime.now(timezone.utc).isoformat()
            previous_stamp = old.get('installed_at') if old else None
            if previous_stamp:
                try:
                    d = datetime.fromisoformat(previous_stamp)
                    if d.tzinfo is None or d > datetime.now(timezone.utc): raise ValueError()
                    stamp = previous_stamp
                except (ValueError, TypeError):
                    raise ValueError('Invalid installation timestamp; inspect recovery')
            backup_id = secrets.token_hex(16)
            manifest = {'schema': 1, 'provider': 'antigravity', 'installed_at': stamp, 'updated_at': datetime.now(timezone.utc).isoformat(),
                        'backup_id': backup_id, 'root_identity': dict(self.root_identity), 'cli_version': support['cli']['version'], 'orchestra': team,
                        'files': {n: {'owned': True, 'sha256': digest(data)} for n, data in desired.items()}}
            receipt = {'schema': 1, 'provider': 'antigravity', 'id': backup_id,
                       'manifest_before': base64.b64encode(before[MANIFEST]).decode() if before[MANIFEST] is not None else None,
                       'files': {n: {'before': base64.b64encode(data).decode() if data is not None else None, 'after': digest(desired.get(n))} for n, data in before.items() if n != MANIFEST}}
            backup_data = encoded(receipt)
            manifest['backup_sha256'] = digest(backup_data)
            desired[MANIFEST] = encoded(manifest)
            after = {n: desired.get(n) for n in before}
            backup_name = META + '/backups/' + backup_id + '.json'
            try:
                write(self.target, backup_name, backup_data, None)
            except Exception as exc:
                raise TransactionError('Antigravity backup failed before managed writes: ' + str(exc), 'backup', 'not_started') from exc
            phases.append({'phase': 'backup', 'status': 'completed'})
            completed = []
            stage = 'applied'
            try:
                for name in sorted(after, key=lambda n: (n == MANIFEST, n)):
                    if before[name] != after[name]:
                        completed.append(name); write(self.target, name, after[name], before[name])
                phases.append({'phase': 'applied', 'status': 'completed'})
                stage = 'verified'
                self.manifest()
                if any(read(self.target, n) != data for n, data in after.items()): raise ValueError('Installed file verification failed')
                phases.append({'phase': 'verified', 'status': 'completed'})
            except Exception as exc:
                conflicts = rollback(self.target, completed, before, after)
                raise TransactionError('Antigravity ' + stage + ' failed; backup retained; rollback ' + ('conflicts retained: ' + ', '.join(conflicts) if conflicts else 'completed') + ': ' + str(exc), stage, 'conflicts_retained' if conflicts else 'rolled_back') from exc
            return {'provider': 'antigravity', 'applied': True, 'verified': True, 'backup_id': backup_id, 'phases': phases, 'capabilities': dict(CAPABILITIES)}

    @bound
    def restore(self, params):
        if params: raise ValueError('Restore takes no fields')
        with locked(self.target):
            m = self.manifest()
            if not m: raise ValueError('No verified Antigravity installation to restore')
            backup_name = META + '/backups/' + m['backup_id'] + '.json'
            raw = read(self.target, backup_name)
            if raw is None or digest(raw) != m.get('backup_sha256'):
                raise ValueError('Antigravity backup integrity failure')
            receipt = json.loads(raw)
            if not isinstance(receipt, dict) or receipt.get('schema') != 1 or receipt.get('provider') != 'antigravity' or receipt.get('id') != m['backup_id'] or not isinstance(receipt.get('files'), dict) or not 1 <= len(receipt['files']) <= 55 or MANIFEST in receipt['files']:
                raise ValueError('Invalid Antigravity backup receipt')
            before, current = {}, {}
            for n, entry in receipt['files'].items():
                if not allowed(n) or not isinstance(entry, dict) or set(entry) != {'before', 'after'}: raise ValueError('Invalid backup path/entry')
                data = read(self.target, n)
                if digest(data) != entry['after']: raise ValueError('Concurrent edit refused during restore: ' + n)
                previous = base64.b64decode(entry['before'], validate=True) if entry['before'] is not None else None
                if previous is not None and len(previous) > MAX_FILE: raise ValueError('Oversize backup')
                before[n], current[n] = previous, data
            previous_manifest = receipt.get('manifest_before')
            before[MANIFEST] = base64.b64decode(previous_manifest, validate=True) if previous_manifest is not None else None
            current[MANIFEST] = read(self.target, MANIFEST)
            if before[MANIFEST] is not None and len(before[MANIFEST]) > MAX_FILE: raise ValueError('Oversize backup manifest')
            # Complete coverage prevents a modified receipt from deleting arbitrary names.
            if not set(m['files']).issubset(current): raise ValueError('Incomplete Antigravity backup coverage')
            completed = []
            try:
                for n in sorted(before, key=lambda n: (n == MANIFEST, n)):
                    completed.append(n); write(self.target, n, before[n], current[n])
                if any(read(self.target, n) != data for n, data in before.items()): raise ValueError('Restore verification failed')
                self.manifest()
            except Exception as exc:
                conflicts = rollback(self.target, completed, current, before)
                raise TransactionError('Antigravity restore failed; rollback ' + ('conflicts retained: ' + ', '.join(conflicts) if conflicts else 'completed') + ': ' + str(exc), 'restore', 'conflicts_retained' if conflicts else 'rolled_back') from exc
            self.pending = None
            return {'provider': 'antigravity', 'restored': True, 'verified': True, 'phases': [{'phase': 'restore', 'status': 'completed'}, {'phase': 'verified', 'status': 'completed'}]}

    @bound
    def call(self, method, params):
        try:
            if method == 'inspect': return self.inspect()
            if method == 'models': return self.models()
            if method == 'usage': return {'provider': 'antigravity', 'status': 'unsupported', 'source': 'none', 'totals': {}, 'records': [], 'limitations': ['No verified trusted project-scoped CLI usage record source; no usage is inferred from IDE files or global quota.']}
            if method == 'preview': return self.preview(params)
            if method == 'apply': return self.apply(params)
            if method == 'restore': return self.restore(params)
            raise ValueError('Unknown Antigravity method')
        except TransactionError:
            raise
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            if method in ('preview', 'apply', 'restore'):
                raise TransactionError(str(exc), 'preflight', 'not_started') from exc
            raise
