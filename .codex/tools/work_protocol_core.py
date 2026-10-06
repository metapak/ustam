"""Controller observed native work protocol. No model calls or inferred sessions.

This file is also installed beside work_protocol.py and has no Ustam dependency.
All state is private, versioned, bounded and atomically replaced under an OS lock.
Worktrees isolate concurrent Git edits; they are not security sandboxes.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager

SCHEMA = 1
MAX_STATE = 8 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_FILES = 2000
PROVIDERS = ('codex', 'claude', 'opencode')
_LOCK = threading.RLock()


class ProtocolError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def atomic(path, value):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > MAX_STATE:
        raise ProtocolError('Work metadata exceeds bounds')
    fd, name = tempfile.mkstemp(prefix='.work-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def relative(value):
    if not isinstance(value, str) or not value or len(value) > 500:
        raise ProtocolError('Bounded relative path required')
    p = Path(value)
    if p.is_absolute() or '..' in p.parts or '.git' in p.parts or '\\' in value:
        raise ProtocolError('Unsafe relative path')
    return p.as_posix()


def safe_file(root, name):
    name = relative(name)
    root = Path(root).resolve()
    path = root / name
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)):
        raise ProtocolError('Symlink or escaped file is unsupported')
    if path.exists() and (not path.is_file() or path.stat().st_size > MAX_FILE):
        raise ProtocolError('File type or size is unsupported')
    return path


def git(root, *args, check=True, input_data=None):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'HOME', 'USERPROFILE', 'TMPDIR', 'TEMP', 'TMP')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    # Command-line overrides only: never modify a user's Git configuration.
    # Even inspection can invoke clean filters, fsmonitor, diff drivers or hooks.
    with tempfile.TemporaryDirectory(prefix='ustam-empty-hooks-') as hooks:
        fixed = ['git', '-c', 'core.hooksPath=' + hooks, '-c', 'core.fsmonitor=false', '-c', 'submodule.recurse=false']
        keys = subprocess.run([*fixed, 'config', '--null', '--name-only', '--get-regexp', r'^filter\..*\.(clean|smudge|process|required)$'],
                              cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        if keys.returncode not in (0, 1) or len(keys.stdout) > 65536:
            raise ProtocolError('Cannot inspect bounded Git filter configuration safely')
        overrides = []
        for raw in keys.stdout.split(b'\0'):
            if not raw:
                continue
            key = raw.decode('utf-8')
            if not key.startswith('filter.') or not key.endswith(('.clean', '.smudge', '.process', '.required')) or any(c in key for c in '\n\r\0'):
                raise ProtocolError('Unsupported Git filter configuration key')
            overrides += ['-c', key + ('=false' if key.endswith('.required') else '=')]
        arguments = list(args)
        if arguments and arguments[0] == 'diff':
            arguments[1:1] = ['--no-ext-diff', '--no-textconv']
        p = subprocess.run([*fixed, *overrides, *arguments], cwd=root, env=env,
                           input=input_data, stdin=subprocess.DEVNULL if input_data is None else None,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if len(p.stdout) > MAX_STATE:
        raise ProtocolError('Git output exceeds bounds')
    if check and p.returncode:
        raise ProtocolError('Git operation failed; inspect worktree and permissions')
    return p.stdout


def checkout_policy(root, head):
    # Effective attributes include global, .git/info, nested and tree attributes.
    # Safe inspection disables all executable filters, irrespective of this gate.
    paths = set(git(root, 'ls-tree', '-r', '-z', '--name-only', head).split(b'\0'))
    paths.update(git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0'))
    paths.discard(b'')
    if len(paths) > MAX_FILES:
        raise ProtocolError('Checkout path count exceeds bounds')
    data = b'\0'.join(sorted(paths)) + b'\0'
    for args in [('check-attr', '-z', '--stdin', 'filter'), ('check-attr', '-z', '--source=' + head, '--stdin', 'filter')]:
        output = git(root, *args, input_data=data)
        records = output.rstrip(b'\0').split(b'\0') if output else []
        if len(records) % 3:
            raise ProtocolError('Invalid effective Git attribute result')
        if any(records[index] not in (b'unspecified', b'unset') for index in range(2, len(records), 3)):
            raise ProtocolError('Effective Git checkout filters require separate permission; operation unsupported')


def snapshot(root):
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    status = git(root, 'status', '--porcelain=v1', '-z')
    paths = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')
    names = sorted(set(os.fsdecode(x) for x in paths if x))
    if len(names) > MAX_FILES:
        raise ProtocolError('Candidate file count exceeds bounds')
    files = {}
    total_bytes = 0
    for name in names:
        path = safe_file(root, name)
        if path.exists():
            total_bytes += path.stat().st_size
            if total_bytes > 64 * 1024 * 1024:
                raise ProtocolError('Candidate byte count exceeds bounds')
        files[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'mode': path.stat().st_mode & 0o777} if path.exists() else None
    return {'head': head, 'status': hashlib.sha256(status).hexdigest(), 'files': files,
            'index': hashlib.sha256(git(root, 'diff', '--cached', '--binary')).hexdigest()}


def contract(value):
    if not isinstance(value, dict) or set(value) - {'scope', 'criteria', 'files', 'commands'}:
        raise ProtocolError('Contract requires scope, criteria, files and optional commands')
    if not isinstance(value.get('scope'), str) or not 1 <= len(value['scope'].strip()) <= 2000:
        raise ProtocolError('Short scope required')
    criteria = value.get('criteria')
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 30 or any(not isinstance(c, str) or not 1 <= len(c) <= 500 for c in criteria):
        raise ProtocolError('Explicit success criteria required')
    files = value.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= 200 or len(files) != len(set(files)):
        raise ProtocolError('Exclusive bounded file ownership required')
    commands = value.get('commands', [])
    if not isinstance(commands, list) or len(commands) > 20:
        raise ProtocolError('Bounded command permission list required')
    for cmd in commands:
        if not isinstance(cmd, list) or not 1 <= len(cmd) <= 30 or any(not isinstance(a, str) or not a or len(a) > 500 for a in cmd):
            raise ProtocolError('Commands must be explicit argv arrays')
    return {'scope': value['scope'].strip(), 'criteria': criteria, 'files': [relative(f) for f in files], 'commands': commands}


def pack(value, c):
    if not isinstance(value, dict) or set(value) != {'author', 'checks', 'good', 'bad'} or value['author'] != 'acceptance_test_author':
        raise ProtocolError('Separate acceptance_test_author must publish test pack before implementation')
    checks = value['checks']
    if not isinstance(checks, list) or not 1 <= len(checks) <= 40:
        raise ProtocolError('Bounded acceptance checks required')
    covered = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {'criterion', 'kind', 'path', 'expected'}:
            raise ProtocolError('Check requires criterion, kind, path and expected')
        if type(check['criterion']) is not int or check['criterion'] not in range(len(c['criteria'])):
            raise ProtocolError('Unknown success criterion')
        covered.add(check['criterion'])
        if check['kind'] not in ('contains', 'json_equals'):
            raise ProtocolError('Unsupported deterministic check type; no passing evidence published')
        if relative(check['path']) not in c['files']:
            raise ProtocolError('Check path outside owned files')
        if check['kind'] == 'contains' and (not isinstance(check['expected'], str) or not check['expected']):
            raise ProtocolError('Nonempty expected text required')
    if covered != set(range(len(c['criteria']))):
        raise ProtocolError('Test pack must cover every success criterion')
    def fixture(value):
        if not isinstance(value, dict) or len(value) > 200:
            raise ProtocolError('Bounded temporary fixture required')
        for name, content in value.items():
            if relative(name) not in c['files'] or not isinstance(content, str) or len(content.encode()) > MAX_FILE:
                raise ProtocolError('Invalid fixture content')
    fixture(value['good'])
    if not isinstance(value['bad'], list) or not 1 <= len(value['bad']) <= 20:
        raise ProtocolError('At least one bad example required')
    for bad in value['bad']:
        fixture(bad)
    for name, data in [('good', value['good']), *[('bad', b) for b in value['bad']]]:
        with tempfile.TemporaryDirectory(prefix='ustam-pack-') as temp:
            for filename, content in data.items():
                path = safe_file(temp, filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            passed = evaluate(temp, checks)
        if passed != (name == 'good'):
            raise ProtocolError('Test quality failed: good reference must pass and every bad example must fail')
    return copy.deepcopy(value)


def evaluate(root, checks):
    for check in checks:
        path = safe_file(root, check['path'])
        if not path.exists():
            return False
        try:
            content = path.read_text(encoding='utf-8')
            result = check['expected'] in content if check['kind'] == 'contains' else json.loads(content) == check['expected']
        except (UnicodeError, ValueError):
            return False
        if not result:
            return False
    return True


class WorkProtocol:
    def __init__(self, directory):
        if Path(directory).is_symlink():
            raise ProtocolError('Symlinked work directory refused')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'works.json'
        self.lockpath = self.directory / '.works.lock'

    @contextmanager
    def locked(self):
        with _LOCK:
            fd = os.open(self.lockpath, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if os.name == 'nt':
                    import msvcrt
                    if not os.fstat(fd).st_size:
                        os.write(fd, b'0')
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def load(self):
        if not self.path.exists():
            return {'schema_version': SCHEMA, 'works': {}, 'operations': {}}
        if self.path.is_symlink() or self.path.stat().st_size > MAX_STATE:
            raise ProtocolError('Invalid work metadata file')
        try:
            data = json.loads(self.path.read_text())
        except (ValueError, UnicodeError) as exc:
            raise ProtocolError('Invalid work metadata; retained for recovery') from exc
        if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data.get('schema_version') != SCHEMA:
            raise ProtocolError('Unsupported work schema; read only, no migration performed')
        if not isinstance(data.get('works'), dict) or not isinstance(data.get('operations'), dict):
            raise ProtocolError('Invalid work metadata shape')
        if len(data['works']) > 300 or len(data['operations']) > 3000:
            raise ProtocolError('Work metadata exceeds record bounds')
        for operation in data['operations'].values():
            if not isinstance(operation, dict) or operation.get('status') not in ('pending', 'failed', 'done') or not isinstance(operation.get('hash'), str):
                raise ProtocolError('Invalid operation intent')
        for key, w in data['works'].items():
            if not isinstance(w, dict) or w.get('id') != key or w.get('provider') not in PROVIDERS or not isinstance(w.get('events'), list):
                raise ProtocolError('Invalid work record')
            if type(w.get('version')) is not int or w['version'] < 1 or not isinstance(w.get('path'), str) or not Path(w['path']).is_absolute() or not isinstance(w.get('project_id'), str):
                raise ProtocolError('Invalid work identity')
            if contract(w.get('contract')) != w['contract'] or digest(w['contract']) != w.get('contract_hash'):
                raise ProtocolError('Invalid work contract binding')
            if not isinstance(w.get('questions'), list) or len(w['questions']) > 100 or len(w['events']) > 1000 or not isinstance(w.get('receipts'), list) or len(w['receipts']) > 100:
                raise ProtocolError('Invalid work evidence bounds')
            for index, event in enumerate(w['events'], 1):
                if not isinstance(event, dict) or event.get('sequence') != index:
                    raise ProtocolError('Invalid event sequence')
            for question in w['questions']:
                if not isinstance(question, dict) or not isinstance(question.get('versions'), dict) or not isinstance(question.get('id'), str) or 'answer' not in question:
                    raise ProtocolError('Invalid decision record')
        return data

    def list(self, project_ids=None):
        with self.locked():
            data = self.load()
            works = []
            for work in data['works'].values():
                if project_ids is None or work['project_id'] in project_ids:
                    work = copy.deepcopy(work)
                    work['pending_operations'] = [key for key, op in data['operations'].items() if op.get('work_id') == work['id'] and op['status'] == 'pending']
                    # Never display a stale successful card after external edits.
                    if work.get('candidate') and work.get('workspace') and work['status'] != 'applied':
                        try:
                            if digest(snapshot(work['workspace'])) != work['candidate']['hash']:
                                work.update(status='candidate_changed', receipts=[], result=None)
                        except (OSError, ProtocolError, subprocess.SubprocessError):
                            work.update(status='needs_attention', result=None)
                    if work.get('integration') and work['status'] == 'ready_to_apply':
                        try:
                            if digest(snapshot(work['path'])) != work['integration']['main_hash']:
                                work.update(status='integration_stale', result=None)
                        except (OSError, ProtocolError, subprocess.SubprocessError):
                            work.update(status='needs_attention', result=None)
                    works.append(work)
            return works

    def event(self, work, action):
        work['events'].append({'sequence': len(work['events']) + 1, 'action': action, 'at': time.time(), 'version': work['version']})
        if len(work['events']) > 1000:
            raise ProtocolError('Event bounds reached')

    def mutate(self, action, payload, operation_id, *, trusted=False):
        if action in ('approve', 'answer', 'apply') and not trusted:
            raise ProtocolError('Human approval requires the trusted Ustam control surface')
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 100 or not all(c.isalnum() or c in '-_' for c in operation_id):
            raise ProtocolError('Explicit operation_id required')
        identity = digest({'action': action, 'payload': payload})
        with self.locked():
            data = self.load()
            prior = data['operations'].get(operation_id)
            if prior:
                if prior['hash'] != identity:
                    raise ProtocolError('Operation ID reused for different input')
                if prior['status'] != 'done':
                    raise ProtocolError('Interrupted operation retained; inspect evidence, do not repeat execution')
                return copy.deepcopy(prior['result'])
            if len(data['operations']) >= 3000 or len(data['works']) >= 300:
                raise ProtocolError('Work history bounds reached')
            # Save intent first. Crashes never silently re-run a command or apply.
            data['operations'][operation_id] = {'hash': identity, 'status': 'pending', 'work_id': payload.get('id')}
            atomic(self.path, data)
            try:
                result = self._action(data, action, payload)
            except Exception as exc:
                data['operations'][operation_id]['status'] = 'failed'
                data['operations'][operation_id]['error'] = type(exc).__name__
                work = data['works'].get(payload.get('id'))
                if work and work['status'] in ('preparing', 'checking', 'integrating', 'applying'):
                    work.update(status='needs_attention', result=None, message='Operation interrupted; workspace and user changes retained')
                    if action in ('integration_check', 'apply'):
                        work['integration'] = None
                atomic(self.path, data)
                raise
            data['operations'][operation_id].update(status='done', result=result)
            atomic(self.path, data)
            return copy.deepcopy(result)

    def _action(self, data, action, p):
        if action == 'create':
            provider = p.get('provider')
            if provider not in PROVIDERS or not isinstance(p.get('project_id'), str) or not p['project_id']:
                raise ProtocolError('Explicit provider and project identity required')
            root = Path(p.get('path', '')).resolve(strict=True)
            if not root.is_dir():
                raise ProtocolError('Project directory required')
            ident = uuid.uuid4().hex
            c = contract(p.get('contract'))
            for other in data['works'].values():
                if other['path'] == str(root) and other['status'] not in ('applied', 'cancelled') and set(c['files']) & set(other['contract']['files']):
                    raise ProtocolError('Another work owns these files')
            w = {'id': ident, 'project_id': p['project_id'], 'path': str(root), 'provider': provider,
                 'version': 1, 'contract': c, 'contract_hash': digest(c), 'approved_hash': None, 'approval_required': bool(c['commands']), 'approval_basis': 'pending_extension' if c['commands'] else 'standing_policy',
                 'policy_hash': digest({'schema': 1, 'operations': ['isolated_prepare', 'deterministic_artifact_check'], 'commands': [], 'main_writes': False}),
                 'status': 'contract', 'events': [], 'questions': [], 'receipts': [], 'result': None,
                 'candidate': None, 'test_pack': None, 'workspace': None, 'source': 'local_protocol', 'author_provenance': 'native_role_declaration', 'controller_scope': 'observes_local_tool_operations_only'}
            data['works'][ident] = w
        else:
            w = data['works'].get(p.get('id'))
            if not w:
                raise ProtocolError('Published work not found')
            if type(p.get('version')) is not int or p['version'] != w['version']:
                raise ProtocolError('Work version changed; refresh before answering or changing it')
            if w['status'] == 'applied':
                raise ProtocolError('Applied work is immutable')
            if action == 'revise':
                c = contract(p.get('contract'))
                if digest(c) != w['contract_hash']:
                    if w['workspace']:
                        raise ProtocolError('Workspace exists; publish a new bounded work instead of expanding active ownership')
                    w.update(contract=c, contract_hash=digest(c), approved_hash=None, version=w['version'] + 1,
                             candidate=None, receipts=[], result=None, test_pack=None, status='contract', approval_required=True, approval_basis='pending_extension')
            elif action == 'approve':
                if p.get('contract_hash') != w['contract_hash']:
                    raise ProtocolError('Approval must bind the displayed contract hash')
                w['approved_hash'] = w['contract_hash']
                w['approval_basis'] = 'trusted_ustam_control'
                w['approval_required'] = False
            elif action == 'question':
                text = p.get('question')
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                    raise ProtocolError('Bounded decision question required')
                options = p.get('options', [])
                if not isinstance(options, list) or len(options) > 10 or any(not isinstance(o, str) or not 1 <= len(o) <= 500 for o in options):
                    raise ProtocolError('Bounded decision options required')
                affected = p.get('affected_tasks', [w['id']])
                if not isinstance(affected, list) or w['id'] not in affected or len(affected) > 30:
                    raise ProtocolError('Explicit affected tasks required')
                versions = {}
                for ident in affected:
                    other = data['works'].get(ident)
                    if not other or other['project_id'] != w['project_id'] or other['provider'] != w['provider']:
                        raise ProtocolError('Decision cannot affect unrelated projects or providers')
                    versions[ident] = other['version']
                key = digest({'text': ' '.join(text.split()).casefold(), 'versions': versions, 'options': p.get('options', [])})
                existing = next((q for other in data['works'].values() for q in other['questions'] if q['key'] == key), None)
                if not existing:
                    question = {'id': uuid.uuid4().hex, 'key': key, 'question': text.strip(), 'versions': versions, 'options': options, 'answer': None}
                    for ident in affected:
                        data['works'][ident]['questions'].append(copy.deepcopy(question))
            elif action == 'answer':
                q = next((q for q in w['questions'] if q['id'] == p.get('question_id')), None)
                if not q or any(data['works'][ident]['version'] != version for ident, version in q['versions'].items()):
                    raise ProtocolError('Decision question is stale')
                if not isinstance(p.get('answer'), str) or not 1 <= len(p['answer']) <= 2000:
                    raise ProtocolError('Bounded scoped answer required')
                if q['answer'] is not None and q['answer'] != p['answer']:
                    raise ProtocolError('Answered decision is immutable')
                for ident in q['versions']:
                    for question in data['works'][ident]['questions']:
                        if question['key'] == q['key']:
                            question['answer'] = p['answer']
            elif action == 'test_pack':
                if w['workspace']:
                    raise ProtocolError('Acceptance pack must precede production implementation')
                w['test_pack'] = pack(p.get('pack'), w['contract'])
                w['test_pack_hash'] = digest(w['test_pack'])
            elif action == 'prepare':
                self._ready(w)
                if not w['test_pack']:
                    raise ProtocolError('Acceptance pack required before production implementation')
                if w['workspace']:
                    raise ProtocolError('Workspace already prepared; resume existing candidate')
                root = Path(w['path'])
                # No git init, reset, clean, commit or user checkout mutation.
                try:
                    head = git(root, 'rev-parse', 'HEAD').decode().strip()
                    checkout_policy(root, head)
                    if Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
                        raise ProtocolError('Project must be repository root')
                    baseline = snapshot(root)
                except ProtocolError as error:
                    w.update(status='unsupported', message=str(error) + '; no repository created')
                    return w
                workspace = self.directory / 'worktrees' / w['id']
                workspace.parent.mkdir(mode=0o700, exist_ok=True)
                w.update(workspace=str(workspace), baseline=baseline, status='preparing')
                atomic(self.path, data)
                checkout_policy(root, baseline['head'])
                git(root, 'worktree', 'add', '--detach', str(workspace), baseline['head'])
                patch_bytes = git(root, 'diff', 'HEAD', '--binary')
                if patch_bytes:
                    self._patch(workspace, patch_bytes)
                for name in git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0'):
                    if name:
                        source = safe_file(root, os.fsdecode(name))
                        dest = safe_file(workspace, os.fsdecode(name))
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, dest)
                if snapshot(root) != baseline:
                    raise ProtocolError('Project changed during context snapshot; workspace retained for inspection')
                w.update(workspace=str(workspace), baseline=baseline, initial_files=snapshot(workspace)['files'], status='implementing')
            elif action == 'freeze':
                self._ready(w)
                if not w['workspace'] or w['status'] not in ('implementing', 'candidate', 'checked', 'candidate_changed'):
                    raise ProtocolError('Prepared active workspace required')
                s = snapshot(w['workspace'])
                self._owned(w, s)
                w.update(candidate={'hash': digest(s), 'snapshot': s, 'pack_hash': w['test_pack_hash'], 'contract_hash': w['contract_hash']}, receipts=[], result=None, status='candidate')
            elif action in ('check', 'reported_check'):
                self._ready(w)
                self._candidate(w)
                if action == 'reported_check':
                    w['receipts'].append({'provenance': 'helper_reported', 'candidate_hash': w['candidate']['hash'], 'status': 'unverified', 'role': p.get('role', 'verifier')})
                    w['result'] = None
                else:
                    w.update(result=None, status='checking')
                    if p.get('role') != 'verifier':
                        raise ProtocolError('Independent read-only verifier receipt required')
                    root = w['workspace']
                    before = snapshot(root)
                    commands = p.get('commands', w['contract']['commands'])
                    if commands != w['contract']['commands']:
                        raise ProtocolError('Every approved required command must run; command set cannot change')
                    passed = evaluate(root, w['test_pack']['checks'])
                    command_receipts = self._commands(w, root)
                    passed = passed and all(r['exit_code'] == 0 for r in command_receipts)
                    self._candidate(w)
                    if snapshot(root) != before:
                        raise ProtocolError('Verifier changed candidate; no check receipt accepted')
                    lock_names = [n for n in before['files'] if Path(n).name in ('package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'uv.lock', 'poetry.lock', 'Cargo.lock', 'requirements.txt', 'pyproject.toml', 'package.json')]
                    receipt = {'provenance': 'controller_observed', 'runner': 'local_work_protocol', 'runner_hash': self._runner_hash(),
                               'role': 'verifier', 'candidate_hash': w['candidate']['hash'], 'contract_hash': w['contract_hash'], 'test_pack_hash': w['test_pack_hash'], 'policy_hash': w['policy_hash'],
                               'environment': {'platform': os.name}, 'lock_config_hash': digest({n: before['files'][n] for n in lock_names}),
                               'commands': command_receipts, 'status': 'passed' if passed else 'failed'}
                    w['receipts'].append(receipt)
                    w.update(status='checked' if passed else 'needs_attention', result=receipt if passed else None)
            elif action == 'integration_check':
                self._ready(w)
                self._candidate(w)
                if not w.get('result'):
                    raise ProtocolError('Current controller observed passing check required')
                w.update(integration=None, status='integrating')
                head = git(w['path'], 'rev-parse', 'HEAD').decode().strip()
                checkout_policy(w['path'], head)
                main = snapshot(w['path'])
                changed = {n for n in set(w['initial_files']) | set(w['candidate']['snapshot']['files']) if w['initial_files'].get(n) != w['candidate']['snapshot']['files'].get(n)}
                if any(main['files'].get(n) != w['baseline']['files'].get(n) for n in changed):
                    raise ProtocolError('Main changed an owned file; conflict requires a new candidate')
                folder = self.directory / 'integration' / uuid.uuid4().hex
                folder.parent.mkdir(mode=0o700, exist_ok=True)
                checkout_policy(w['path'], main['head'])
                git(w['path'], 'worktree', 'add', '--detach', str(folder), main['head'])
                try:
                    patch_bytes = git(w['path'], 'diff', 'HEAD', '--binary')
                    if patch_bytes:
                        self._patch(folder, patch_bytes)
                    for name in git(w['path'], 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0'):
                        if name:
                            source = safe_file(w['path'], os.fsdecode(name))
                            dest = safe_file(folder, os.fsdecode(name))
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, dest)
                    self._copy_changes(w, folder, changed)
                    before = snapshot(folder)
                    passed = evaluate(folder, w['test_pack']['checks'])
                    command_receipts = self._commands(w, folder)
                    passed = passed and all(r['exit_code'] == 0 for r in command_receipts)
                    if snapshot(folder) != before:
                        raise ProtocolError('Combined tree check changed candidate')
                    if snapshot(w['path']) != main:
                        raise ProtocolError('Main changed during integration; check again')
                    w['integration'] = {'main_hash': digest(main), 'candidate_hash': w['candidate']['hash'], 'pack_hash': w['test_pack_hash'], 'contract_hash': w['contract_hash'], 'policy_hash': w['policy_hash'], 'runner': 'local_work_protocol', 'runner_hash': self._runner_hash(), 'provenance': 'controller_observed', 'commands': command_receipts, 'combined_tree_hash': digest(before), 'status': 'passed' if passed else 'failed', 'files': sorted(changed)}
                    w['integration_hash'] = digest(w['integration'])
                    w['status'] = 'ready_to_apply' if passed else 'needs_attention'
                finally:
                    git(w['path'], 'worktree', 'remove', '--force', str(folder), check=False)
            elif action == 'apply':
                self._ready(w)
                self._candidate(w)
                integration = w.get('integration') or {}
                if w['status'] != 'ready_to_apply':
                    raise ProtocolError('Current combined tree approval candidate required')
                result = w.get('result') or {}
                for key, expected in (('candidate_hash', w['candidate']['hash']), ('contract_hash', w['contract_hash']), ('policy_hash', w['policy_hash'])):
                    if result.get(key) != expected or integration.get(key) != expected:
                        raise ProtocolError('Receipt binding mismatch; rerun required checks')
                if result.get('test_pack_hash') != w['test_pack_hash'] or integration.get('pack_hash') != w['test_pack_hash']:
                    raise ProtocolError('Receipt test pack binding mismatch')
                self._receipt_tools(result)
                self._receipt_tools(integration)
                if integration.get('status') != 'passed' or integration.get('candidate_hash') != w['candidate']['hash'] or integration.get('main_hash') != digest(snapshot(w['path'])):
                    raise ProtocolError('Combined tree check stale; revalidate before apply')
                if p.get('integration_hash') != digest(integration) or p.get('approve') is not True:
                    raise ProtocolError('One explicit apply approval must bind combined tree evidence')
                changed = integration['files']
                w['status'] = 'applying'
                atomic(self.path, data)
                originals = {name: (safe_file(w['path'], name).read_bytes(), safe_file(w['path'], name).stat().st_mode & 0o777) if safe_file(w['path'], name).exists() else None for name in changed}
                try:
                    self._copy_changes(w, w['path'], changed)
                except BaseException:
                    for name, content in originals.items():
                        dest = safe_file(w['path'], name)
                        if content is None:
                            dest.unlink(missing_ok=True)
                        else:
                            dest.write_bytes(content[0])
                            dest.chmod(content[1])
                    raise
                w.update(status='applied', result={'status': 'applied', 'candidate_hash': w['candidate']['hash'], 'integration_hash': digest(integration), 'files': changed})
            elif action == 'cancel':
                w.update(status='cancelled', result=None)
            elif action == 'resume':
                if w['status'] != 'cancelled':
                    raise ProtocolError('Only cancelled work resumes; interrupted operations require inspection')
                if w['workspace'] and 'initial_files' not in w:
                    raise ProtocolError('Preparation interrupted; inspect retained workspace before publishing new work')
                w.update(status='implementing' if w['workspace'] else 'contract', result=None)
            else:
                raise ProtocolError('Unknown local protocol action')
        self.event(w, action)
        return w

    def _receipt_tools(self, receipt):
        required_hashes = ('runner_hash', 'candidate_hash', 'contract_hash', 'policy_hash')
        if any(not isinstance(receipt.get(k), str) or len(receipt[k]) != 64 or any(c not in '0123456789abcdef' for c in receipt[k]) for k in required_hashes):
            raise ProtocolError('Incomplete observed receipt binding')
        if receipt.get('provenance') != 'controller_observed' or receipt.get('status') != 'passed' or receipt.get('runner_hash') != self._runner_hash():
            raise ProtocolError('Observed runner evidence changed; rerun required checks')
        for command in receipt.get('commands', []):
            path = Path(command['executable'])
            if not path.is_file() or path.stat().st_size > 100 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != command['executable_hash']:
                raise ProtocolError('Check tool changed; rerun required checks')

    @staticmethod
    def _runner_hash():
        source = Path(__file__)
        if not source.is_file() or source.is_symlink():
            raise ProtocolError('Runner source resource unavailable; no verified receipt emitted')
        return hashlib.sha256(source.read_bytes()).hexdigest()

    def _commands(self, w, root):
        commands = w['contract']['commands']
        if commands and (w['approved_hash'] != w['contract_hash'] or w['approval_basis'] != 'trusted_ustam_control'):
            raise ProtocolError('Command extension requires trusted control authorization')
        receipts = []
        for argv in commands:
            executable = shutil.which(argv[0])
            if not executable or Path(executable).resolve().is_relative_to(Path(root)) or Path(executable).resolve().is_relative_to(Path(w['path'])):
                raise ProtocolError('Command tool unavailable or inside project')
            executable = Path(executable).resolve()
            # No unapproved --version subprocess. Hash exact executable; disclose unknown version.
            if executable.stat().st_size > 100 * 1024 * 1024:
                raise ProtocolError('Command executable exceeds hash bounds')
            tool_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
            proc = subprocess.run([str(executable), *argv[1:]], cwd=root, env=self._environment(), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            receipts.append({'argv': argv, 'executable': str(executable), 'executable_hash': tool_hash,
                             'tool_version': 'unavailable_without_separate_permission', 'cwd': str(Path(root).resolve()),
                             'environment_keys': sorted(self._environment()), 'exit_code': proc.returncode})
        return receipts

    @staticmethod
    def _environment():
        # No provider credentials, prompt, raw environment or secret output persisted.
        return {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TMPDIR', 'TEMP', 'TMP')}

    @staticmethod
    def _patch(root, content):
        git(root, 'apply', '--binary', input_data=content)

    @staticmethod
    def _owned(w, s):
        if s['head'] != w['baseline']['head']:
            raise ProtocolError('Workspace HEAD changed; commits are not protocol implementation')
        changed = {n for n in set(w['initial_files']) | set(s['files']) if w['initial_files'].get(n) != s['files'].get(n)}
        if not changed <= set(w['contract']['files']):
            raise ProtocolError('Candidate changed files outside exclusive ownership')

    def _candidate(self, w):
        if not w['candidate'] or digest(snapshot(w['workspace'])) != w['candidate']['hash']:
            w.update(receipts=[], result=None, status='candidate_changed')
            raise ProtocolError('Candidate changed; freeze again and rerun checks')
        if w['candidate']['pack_hash'] != w['test_pack_hash'] or w['candidate']['contract_hash'] != w['contract_hash']:
            raise ProtocolError('Contract or test pack changed; stale candidate')

    @staticmethod
    def _ready(w):
        if w['status'] == 'cancelled':
            raise ProtocolError('Work cancelled; candidate retained, explicit resume required')
        if w.get('approval_required') and w['approved_hash'] != w['contract_hash']:
            raise ProtocolError('Material contract change requires scoped approval')
        if any(q['answer'] is None and q['versions'].get(w['id']) == w['version'] for q in w['questions']):
            raise ProtocolError('Affected decision question awaits answer')

    @staticmethod
    def _copy_changes(w, root, changed):
        for name in changed:
            source = safe_file(w['workspace'], name)
            dest = safe_file(root, name)
            expected = w['candidate']['snapshot']['files'].get(name)
            if expected is None:
                if source.exists():
                    raise ProtocolError('Deleted candidate file changed during integration')
                dest.unlink(missing_ok=True)
                continue
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected['sha256'] or source.stat().st_mode & 0o777 != expected['mode']:
                raise ProtocolError('Candidate file changed during integration')
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, filename = tempfile.mkstemp(prefix='.ustam-apply-', dir=dest.parent)
            try:
                with os.fdopen(fd, 'wb') as out:
                    out.write(content)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(filename, expected['mode'])
                os.replace(filename, dest)
            finally:
                if os.path.exists(filename):
                    os.unlink(filename)
