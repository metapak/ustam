"""Fixed provider commands, bounded streams and native read-only chief roles."""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import base64
import secrets
import urllib.parse
import urllib.request


class RuntimeAttention(Exception):
    pass


def _known_cli_bins(environment, platform):
    """A small installer-directory allowlist; never scans projects or app bundles."""
    home = Path(environment.get('USERPROFILE') or Path.home()) if platform == 'nt' else Path.home()
    bins = [home / '.local' / 'bin', home / '.codex' / 'packages' / 'standalone' / 'current' / 'bin']
    if platform == 'nt':
        local = environment.get('LOCALAPPDATA')
        roaming = environment.get('APPDATA')
        if local:
            bins.append(Path(local) / 'agy' / 'bin')
            bins.append(Path(local) / 'Microsoft' / 'WinGet' / 'Links')
        if roaming:
            bins.append(Path(roaming) / 'npm')
    else:
        bins += [Path('/usr/local/bin'), Path('/opt/homebrew/bin'), Path('/usr/bin'), Path('/bin'), home / '.opencode' / 'bin']
    return bins


def _outside_workspace(path, project=None, directory=False):
    cwd = Path.cwd().resolve()
    lexical = Path(os.path.abspath(path))
    resolved = Path(path).resolve()
    # A desktop launch may have cwd=/ or cwd=home. Exclude direct cwd lookup,
    # rather than excluding every installed binary beneath that ancestor.
    if directory:
        if lexical == cwd or resolved == cwd:
            return False
    elif lexical.parent == cwd or resolved.parent == cwd:
        return False
    if project:
        root = Path(project).resolve()
        if lexical.is_relative_to(root) or resolved.is_relative_to(root):
            return False
    return True


def _cli_bins(environment, project=None, platform=None):
    platform = platform or os.name
    separator = ';' if platform == 'nt' else os.pathsep
    candidates = [Path(value) for value in environment.get('PATH', '').split(separator) if value and Path(value).is_absolute()]
    candidates += _known_cli_bins(environment, platform)
    found, seen = [], set()
    for candidate in candidates:
        if not _outside_workspace(candidate, project, directory=True):
            continue
        key = os.path.normcase(str(candidate.resolve()))
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    return found


def resolve_cli(provider, project=None, env=None, platform=None):
    """Find a fixed CLI outside project/cwd, including desktop launch PATH fallbacks."""
    if provider not in ('codex', 'claude', 'opencode', 'antigravity'):
        raise RuntimeAttention('Unsupported provider CLI.')
    environment = dict(os.environ if env is None else env)
    platform = platform or os.name
    bins = _cli_bins(environment, project, platform)
    command = 'agy' if provider == 'antigravity' else provider
    separator = ';' if platform == 'nt' else os.pathsep
    # On Windows only native executables reach Popen; batch shims can invoke cmd.exe
    # even with shell=False and cannot safely receive an arbitrary user prompt.
    if platform == 'nt':
        candidates = [directory / (command + '.exe') for directory in bins]
    else:
        candidate = shutil.which(command, path=separator.join(str(directory) for directory in bins))
        candidates = ([Path(candidate)] if candidate else []) + [directory / command for directory in bins]
    for candidate in candidates:
        if candidate.is_file() and _outside_workspace(candidate, project):
            resolved = candidate.resolve()
            if platform == 'nt' and resolved.suffix.lower() != '.exe':
                continue
            if platform == 'nt' or os.access(resolved, os.X_OK):
                return str(resolved)
    if platform == 'nt' and any((directory / (command + extension)).is_file() for directory in bins for extension in ('.cmd', '.bat', '.CMD', '.BAT')):
        raise RuntimeAttention(f'{provider} batch shim cannot safely receive job prompts. Install its native CLI executable and sign in separately.')
    raise RuntimeAttention(f'Install {provider} and sign in with its own CLI before starting.')


def cli_environment(executable, project=None, env=None, platform=None):
    """Preserve auth/environment, prepend trusted bins for Unix interpreter wrappers."""
    environment = dict(os.environ if env is None else env)
    platform = platform or os.name
    path = Path(executable)
    if not path.is_file() or not _outside_workspace(path, project) or (platform == 'nt' and path.resolve().suffix.lower() != '.exe'):
        raise RuntimeAttention('Provider executable must be installed outside the selected project and current directory.')
    bins = [path.resolve().parent] + _cli_bins(environment, project, platform)
    separator = ';' if platform == 'nt' else os.pathsep
    environment['PATH'] = separator.join(dict.fromkeys(str(directory) for directory in bins))
    return environment


class Runtime:
    MAX_HELPER_SLOTS = 50
    REQUIRED_ROLES = ('explorer', 'implementer', 'verifier', 'reviewer')
    ROLES = ('acceptance_test_author', 'fast_lookup', 'explorer', 'researcher', 'implementer', 'verifier', 'reviewer', 'failure_analyst', 'qa_operator', 'advisor')
    CHIEF = ('You are the read-only chief. Never implement, edit files, run shell commands, or inspect files yourself. '
             'Only plan, delegate to fixed registered roles, read their compact summaries, and report. '
             'Delegate inspection to explorer; assign implementer explicit bounded file ownership and acceptance criteria. '
             'There must be only one writer. Freeze the candidate before independent verifier and reviewer. '
             'Do not delegate beyond one level. At most two implementation attempts, two verification attempts and two reviews. '
             'Stop and report needs_attention for authentication, permissions, new approval, missing role, or scope expansion. '
             'Never commit, push, publish, deploy, change permissions or install dependencies. '
             'Final response must be JSON with status (completed or needs_attention) and a compact summary.')

    @staticmethod
    def job_capabilities(provider):
        """Report Hub job limits, separately from saved native team settings."""
        if provider not in ('codex', 'claude', 'opencode', 'antigravity'):
            raise ValueError('Unsupported provider')
        if provider == 'antigravity':
            return {'enabled': False, 'supported_roles': [], 'execution_roles': [], 'required_roles': [], 'max_helper_slots': 0, 'helper_concurrency': 0, 'reason': 'Antigravity unattended permission and completion contracts are unverified; use the manual Works bridge.'}
        supported = [role for role in Runtime.ROLES if provider != 'claude' or role != 'fast_lookup']
        execution = Runtime.REQUIRED_ROLES if provider == 'opencode' else supported
        return {'max_helper_slots': Runtime.MAX_HELPER_SLOTS,
                'required_roles': [role.replace('_', '-') for role in Runtime.REQUIRED_ROLES],
                'supported_roles': [role.replace('_', '-') for role in supported],
                'execution_roles': [role.replace('_', '-') for role in execution],
                'duplicate_role_policy': 'first', 'helper_concurrency': 1}

    def __init__(self, state_dir, max_seconds=900, max_output=1024 * 1024):
        self.directory = Path(state_dir)
        self.max_seconds = max_seconds
        self.max_output = max_output
        self.commands = {}

    def prepare(self, job, resume=False):
        provider = job['provider']
        if provider not in ('codex', 'claude', 'opencode'):
            raise RuntimeAttention('This provider has no supported job command.')
        executable = resolve_cli(provider, project=job['path'])
        environment = cli_environment(executable, project=job['path'])
        try:
            result = subprocess.run([executable, 'exec', '--help'] if provider == 'codex' else [executable, '--help'],
                                    capture_output=True, timeout=10, check=False, env=environment)
            help_text = result.stdout[:65536].decode('utf-8', 'replace')
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeAttention(f'{provider} capability check failed. Check its installation.')
        if provider == 'opencode':
            version = subprocess.run([executable, '--version'], capture_output=True, timeout=10, env=environment)
            run_help = subprocess.run([executable, 'run', '--help'], capture_output=True, timeout=10, env=environment)
            text = run_help.stdout.decode('utf-8', 'replace')
            if version.returncode or version.stdout.decode().strip().removeprefix('v') != '2.0.3' or not all(flag in text for flag in ('--standalone', '--model', '--agent', '--format', 'provider/model#variant')):
                raise RuntimeAttention('This OpenCode version has not verified the native sequential permissions contract; use verified CLI 2.0.3.')
            if resume:
                raise RuntimeAttention('OpenCode sequential jobs use separate role sessions. Review changes and create a new plan; resuming one worker as chief is unsafe.')
            for selection in [job['orchestra']['chief']] + job['plan']['execution_helpers']:
                if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:/-]+', selection['model']):
                    raise RuntimeAttention('OpenCode requires an explicit provider/model with effort selected separately.')
            if not set(self.REQUIRED_ROLES) <= {h['role'] for h in job['plan']['execution_helpers']}:
                raise RuntimeAttention('Select explorer, implementer, verifier and reviewer helpers before starting.')
            self.commands[job['id']] = executable
            return
        if provider == 'claude':
            flags = ('--agents', '--agent', '--restricted', '--permission-prompts', '--effort', '--json-schema', '--tools', '--resume')
            if result.returncode or not all(flag in help_text for flag in flags):
                raise RuntimeAttention('Installed Claude lacks required restricted native chief capabilities; update its CLI.')
            selections = [job['orchestra']['chief']] + job['plan']['execution_helpers']
            if any(h['effort'] not in ('low', 'medium', 'high', 'xhigh', 'max') for h in selections):
                raise RuntimeAttention('Claude does not support the selected effort; choose a supported level.')
        elif result.returncode or not all(flag in help_text for flag in ('--json', '--sandbox', 'read-only', '--config', '--output-schema')):
            raise RuntimeAttention('Installed Codex lacks required read-only and structured-output capabilities; update its CLI.')
        helpers = job['plan']['execution_helpers']
        roles = {h['role'] for h in helpers}
        if not set(self.REQUIRED_ROLES) <= roles:
            raise RuntimeAttention('Select explorer, implementer, verifier and reviewer helpers before starting.')
        if resume and provider == 'codex':
            # Resume must accept the same security overrides; never fall back to a bare session command.
            result = subprocess.run([executable, 'exec', 'resume', '--help'], capture_output=True, timeout=10, env=environment)
            text = result.stdout[:65536].decode('utf-8', 'replace')
            if result.returncode or not all(flag in text for flag in ('--json', '--config', '--model')):
                raise RuntimeAttention('Installed Codex cannot resume with required permission overrides. Create a new plan.')
        self.commands[job['id']] = executable

    def _command(self, job, resume):
        if job['provider'] == 'opencode':
            selection = job['_selection']
            return [self.commands[job['id']], 'run', '--server', job['_server_url'], '--format', 'json',
                    '--agent', 'ustam-phase', '--model', selection['model'] + '#' + selection['effort'], job['_prompt']]
        if job['provider'] == 'claude':
            return self._claude_command(job, resume)
        directory = self.directory / job['id']
        directory.mkdir(mode=0o700, exist_ok=True)
        agents = {'enabled': True, 'max_concurrent_threads_per_session': 1, 'max_depth': 1}
        for helper in job['plan']['execution_helpers']:
            role = helper['role']
            policy = 'workspace-write' if role == 'implementer' else 'read-only'
            instructions = ('You are the sole bounded implementer. Edit only assigned files. No delegation. '
                            'No commit, push, deploy, dependencies or permission changes. Stop after two failed attempts. '
                            'Report changes, tests, acceptance and risks.') if role == 'implementer' else (
                            f'You are the independent {role}. Read-only: do not edit files, delegate, or run commands that modify the project. '
                            'Use the assigned bounded scope. Return compact evidence, findings and limitations.')
            path = directory / (role + '.toml')
            path.write_text('model = ' + json.dumps(helper['model']) + '\nmodel_reasoning_effort = ' + json.dumps(helper['effort']) +
                            '\nsandbox_mode = ' + json.dumps(policy) + '\ndeveloper_instructions = ' + json.dumps(instructions) +
                            '\n[agents]\nenabled = false\n', encoding='utf-8')
            os.chmod(path, 0o600)
            agents[role] = {'description': f'Bounded {role}', 'config_file': str(path)}
        # JSON strings/objects are valid TOML inline values except object colon syntax.
        def toml(value):
            if isinstance(value, dict):
                return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
            return json.dumps(value)
        chief = job['orchestra']['chief']
        argv = [self.commands[job['id']], 'exec']
        if resume:
            argv += ['resume']
        argv += ['--json', '-m', chief['model'], '-c', 'model_reasoning_effort=' + json.dumps(chief['effort']),
                 '-c', 'sandbox_mode="read-only"', '-c', 'approval_policy="untrusted"',
                 '-c', 'agents=' + toml(agents), '-c', 'developer_instructions=' + json.dumps(self.CHIEF)]
        schema = directory / 'result-schema.json'
        if not resume:
            schema.write_text(json.dumps({'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['completed', 'needs_attention']}, 'summary': {'type': 'string'}}, 'required': ['status', 'summary'], 'additionalProperties': False}))
            argv += ['--sandbox', 'read-only', '--output-schema', str(schema)]
        else:
            argv += ['--output-schema', str(schema), job['session_id']]
        argv += ['Approved proposed plan. Task: ' + job['task']]
        return argv

    def _claude_command(self, job, resume):
        chief = job['orchestra']['chief']
        definitions = {'ustam-chief': {'description': 'Read-only coordinator', 'prompt': self.CHIEF,
                       'tools': ['Agent(' + ','.join(h['role'] for h in job['plan']['execution_helpers']) + ')'],
                       'model': chief['model'], 'effort': chief['effort'], 'maxTurns': 36}}
        for helper in job['plan']['execution_helpers']:
            role = helper['role']
            tools = ['Read', 'Glob', 'Grep'] + (['Edit', 'Write'] if role == 'implementer' else [])
            definitions[role] = {'description': 'Bounded ' + role, 'model': helper['model'], 'effort': helper['effort'],
                                 'tools': tools, 'maxTurns': 20,
                                 'prompt': 'You are the ' + role + '. No delegation. Only assigned scope. '
                                 'One writer, at most two attempts. Never publish or change dependencies or permissions. '
                                 'Shell execution is unavailable: report needs_attention when command tests are necessary; do not claim tests ran.'}
        schema = {'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['completed', 'needs_attention']},
                  'summary': {'type': 'string'}}, 'required': ['status', 'summary'], 'additionalProperties': False}
        argv = [self.commands[job['id']], '-p', '--output-format', 'stream-json', '--verbose',
                '--model', chief['model'], '--effort', chief['effort'], '--agents', json.dumps(definitions),
                '--agent', 'ustam-chief', '--restricted', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--tools', 'Agent,Read,Glob,Grep,Edit,Write', '--allowedTools', 'Read,Glob,Grep,Edit,Write',
                '--permission-mode', 'manual', '--permission-prompts', 'none',
                '--json-schema', json.dumps(schema)]
        if resume:
            argv += ['--resume', job['session_id']]
        return argv + ['Approved proposed plan. Task: ' + job['task']]

    def _lease(self, project):
        path = self.directory / ('project-' + hashlib.sha256(os.path.normcase(project).encode()).hexdigest() + '.lock')
        handle = open(path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                if handle.read(1) == b'':
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeAttention('Another hub is running a job for this project.')
        return handle

    @staticmethod
    def _stop(process):
        if getattr(process, '_ustam_stopped', False):
            return
        exited = process.poll() is not None
        if os.name != 'nt':
            if process.pid == os.getpgrp():
                raise RuntimeAttention('Refusing to signal the hub process group.')
            try:
                if os.getpgid(process.pid) != process.pid:
                    raise RuntimeAttention('Provider process is outside its owned process group.')
            except ProcessLookupError:
                pass
        if os.name == 'nt':
            if not exited:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=5)
        else:
            # The leader may already have exited while descendants retain its group.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS may reject signals to a group whose leader is a zombie.
                if process.poll() is None:
                    try:
                        process.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        raise
        process.wait(timeout=5)
        process._ustam_stopped = True

    @staticmethod
    def _opencode_policy(job):
        policy = [{'action': '*', 'resource': '*', 'effect': 'deny'}]
        if job['_role'] != 'chief':
            policy += [{'action': action, 'resource': '*', 'effect': 'allow'}
                       for action in ('read', 'glob', 'grep')]
        if job['_role'] == 'implementer':
            policy += [{'action': 'edit', 'resource': path.replace('\\', '/'), 'effect': 'allow'}
                       for path in job['_ownership']]
        if job['_role'] != 'chief':
            policy += [{'action': action, 'resource': pattern, 'effect': 'deny'}
                       for action in ('read', 'glob', 'grep') for pattern in ('/*', '*:*')]
        return policy

    def _opencode_environment(self, job):
        directory = self.directory / job['id'] / 'opencode-config'
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        policy = self._opencode_policy(job)
        selection = job['_selection']
        config = {'default_agent': 'ustam-phase', 'agents': {'ustam-phase': {
                  'description': 'Sequential bounded ' + job['_role'], 'mode': 'primary', 'steps': 20,
                  'system': ('You are the read-only chief. No tools, no execution, no self-delegation. The Ustam controller delegates to fixed roles sequentially. '
                             'Only produce a bounded JSON plan from explorer summary or evaluate compact worker summaries. '
                             'No scope expansion, permissions, dependencies or publishing. Report unresolved limitations.') if job['_role'] == 'chief' else
                  'You are the bounded ' + job['_role'] + '. Only assigned files. No delegation, shell, permissions, dependencies, commit or publishing. '
                  'Return JSON {"status":"completed"|"needs_attention","summary":"compact evidence"}. '
                  'Shell tests are unavailable; report needs_attention if needed. At most two attempts.',
                  'model': selection['model'] + '#' + selection['effort'], 'permissions': policy}}}
        return {'OPENCODE_CONFIG_DIR': str(directory), 'OPENCODE_CONFIG_PROJECT_DISABLE': '1',
                'OPENCODE_CONFIG_CONTENT': json.dumps(config)}

    def _opencode_server(self, job, environment, cancel):
        token = secrets.token_urlsafe(32)
        environment['OPENCODE_PASSWORD'] = token
        kwargs = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        server = subprocess.Popen([self.commands[job['id']], 'serve', '--stdio', '--port', '0'], cwd=job['path'],
                                  env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, **kwargs)
        lines = queue.Queue(maxsize=1)
        def first_line():
            lines.put(server.stdout.readline(4096))
        thread = threading.Thread(target=first_line, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + min(15, self.max_seconds)
            line = None
            while time.monotonic() < deadline and not cancel.is_set():
                try:
                    line = lines.get(timeout=.1)
                    break
                except queue.Empty:
                    continue
            if not line:
                raise RuntimeAttention('OpenCode private permission server did not start in time.')
            address = json.loads(line).get('url', '')
            parsed = urllib.parse.urlparse(address)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise RuntimeAttention('OpenCode returned an invalid private server address.')
            authentication = base64.b64encode(('opencode:' + token).encode()).decode()
            request = urllib.request.Request(address + '/api/agent?' + urllib.parse.urlencode({'directory': job['path']}),
                                             headers={'Authorization': 'Basic ' + authentication})
            while time.monotonic() < deadline and not cancel.is_set():
                try:
                    # Never forward the private server token or response to hub metadata.
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=min(2, max(.1, deadline-time.monotonic()))) as response:
                        raw = response.read(512 * 1024 + 1)
                    if len(raw) > 512 * 1024:
                        raise RuntimeAttention('OpenCode permission response exceeded its limit.')
                    loaded = json.loads(raw)
                    agents = loaded.get('data', []) if isinstance(loaded, dict) else loaded
                    found = next((a for a in agents if isinstance(a, dict) and a.get('id') == 'ustam-phase'), None)
                    if found:
                        self._verify_opencode_agent(job, found)
                        return server, address
                except (OSError, ValueError):
                    pass
                cancel.wait(.2)
            raise RuntimeAttention('OpenCode did not load the requested private agent permissions.')
        except Exception:
            self._stop(server)
            if server.stdin:
                server.stdin.close()
            server.stdout.close()
            thread.join(timeout=1)
            raise

    @staticmethod
    def _verify_opencode_agent(job, agent):
        rules = agent.get('permissions')
        expected_rules = Runtime._opencode_policy(job)
        if not isinstance(rules, list) or rules[-len(expected_rules):] != expected_rules:
            raise RuntimeAttention('OpenCode did not load the exact requested native permission rules.')
        # All rules we generate are literals, the all-resource wildcard, or owned files.
        def effect(action, resource):
            result = None
            for rule in rules:
                if not isinstance(rule, dict):
                    raise RuntimeAttention('Malformed native permission rule.')
                if rule.get('action') in ('*', action) and rule.get('resource') in ('*', resource):
                    result = rule.get('effect')
            return result
        for action in ('shell', 'subagent'):
            if effect(action, 'anything') != 'deny':
                raise RuntimeAttention('OpenCode agent retained execution or delegation permissions.')
        if effect('edit', '__ustam_unowned__') != 'deny':
            raise RuntimeAttention('OpenCode worker can edit outside its ownership.')
        if job['_role'] == 'chief' and effect('read', 'anything') != 'deny':
            raise RuntimeAttention('OpenCode chief retained file access.')
        for owned in job.get('_ownership', []):
            if job['_role'] == 'implementer' and effect('edit', owned.replace('\\', '/')) != 'allow':
                raise RuntimeAttention('OpenCode implementer did not load its bounded file permissions.')
        expected = job['_selection']
        model = agent.get('model')
        provider, name = expected['model'].split('/', 1)
        if not isinstance(model, dict) or model.get('providerID') != provider or model.get('id', model.get('model')) != name or model.get('variant') != expected['effort']:
            raise RuntimeAttention('OpenCode agent model and effort do not match the approved orchestra.')

    @staticmethod
    def _ownership(project, values):
        if not isinstance(values, list) or not values or len(values) > 100:
            raise RuntimeAttention('Chief must return 1–100 explicit owned file paths.')
        clean = []
        root = Path(project).resolve()
        for value in values:
            if not isinstance(value, str) or not value or len(value) > 512 or any(char in value for char in '*?[]{}\\:'):
                raise RuntimeAttention('Invalid chief file ownership.')
            path = Path(value)
            if path.is_absolute() or '..' in path.parts or any(part in ('.git', '.codex', '.claude', '.opencode') for part in path.parts):
                raise RuntimeAttention('Chief ownership expands into protected paths.')
            target = (root / path).resolve()
            if not target.is_relative_to(root) or target.is_dir():
                raise RuntimeAttention('Chief ownership must name files inside the project.')
            clean.append(str(path))
        return list(dict.fromkeys(clean))

    def run(self, job, cancel, emit, resume=False):
        if job['provider'] != 'opencode':
            return self._run_one(job, cancel, emit, resume)
        if resume:
            return {'status': 'needs_attention', 'message': 'Create a new plan for sequential OpenCode execution.'}
        try:
            lease = self._lease(job['path'])
        except RuntimeAttention as exc:
            return {'status': 'needs_attention', 'message': str(exc)}
        started, remaining_output = time.monotonic(), self.max_output
        helpers = {h['role']: h for h in job['plan']['execution_helpers']}
        # The controller owns ordering. The chief has no tools, including delegation.
        def phase(role, prompt, ownership=None):
            nonlocal remaining_output
            remaining = self.max_seconds - (time.monotonic() - started)
            if remaining <= 0 or remaining_output <= 0:
                raise RuntimeAttention('Sequential job reached its time or output limit.')
            current = dict(job, _role=role, _selection=job['orchestra']['chief'] if role == 'chief' else helpers[role],
                           _prompt=prompt, _ownership=ownership or [])
            bounded = Runtime(self.directory, max_seconds=remaining, max_output=remaining_output)
            bounded.commands = self.commands
            emit({'type': 'phase', 'role': role, 'model': current['_selection']['model'], 'effort': current['_selection']['effort']})
            result = bounded._run_one(current, cancel, emit, lock_project=False)
            remaining_output -= result.get('_output_bytes', 0)
            if result['status'] != 'completed':
                raise RuntimeAttention(result['message'])
            return result['_response']
        try:
            explored = phase('explorer', 'Read only the project. Return JSON status and compact summary of relevant files/constraints. Task: ' + job['task'])
            if explored.get('status') != 'completed':
                raise RuntimeAttention('Explorer requires attention before implementation.')
            planned = phase('chief', 'Do not use any tools or spawn workers. The Ustam controller delegates sequentially. '
                            'Return ONLY JSON {"ownership":["relative/file"],"brief":"bounded implementation instructions"}. '
                            'Scope must satisfy the approved task; no new permissions, dependencies, publishing or architecture. '
                            'Task: ' + job['task'] + '\nExplorer summary: ' + str(explored.get('summary', ''))[:6000])
            ownership = self._ownership(job['path'], planned.get('ownership'))
            brief = planned.get('brief')
            if not isinstance(brief, str) or not brief or len(brief) > 8000:
                raise RuntimeAttention('Chief did not return a bounded implementation brief.')
            implemented = phase('implementer', 'Owned files: ' + json.dumps(ownership) + '\n' + brief, ownership)
            if implemented.get('status') != 'completed':
                raise RuntimeAttention('Implementer requires attention; existing changes are retained.')
            def frozen():
                return {path: hashlib.sha256((Path(job['path']) / path).read_bytes()).hexdigest()
                        if (Path(job['path']) / path).is_file() else None for path in ownership}
            candidate = frozen()
            evidence = 'Task: ' + job['task'] + '\nOwned frozen files: ' + json.dumps(ownership) + '\nImplementation summary: ' + str(implemented.get('summary', ''))[:6000]
            for role in ('verifier', 'reviewer'):
                checked = phase(role, evidence)
                if frozen() != candidate:
                    raise RuntimeAttention('Frozen candidate changed during independent checking.')
                if checked.get('status') != 'completed':
                    raise RuntimeAttention(role.title() + ' requires attention; existing changes are retained.')
                evidence += '\n' + role.title() + ' summary: ' + str(checked.get('summary', ''))[:3000]
            completed = phase('chief', 'Read these compact worker summaries only; return JSON status completed or needs_attention and summary. ' + evidence)
            if completed.get('status') != 'completed':
                raise RuntimeAttention('Chief reported unresolved limitations.')
            return {'status': 'completed', 'message': 'Sequential chief and workers reported completion; inspect project changes and evidence.'}
        except RuntimeAttention as exc:
            return {'status': 'cancelled' if cancel.is_set() else 'needs_attention', 'message': str(exc)}
        finally:
            lease.close()

    @staticmethod
    def _attention_message(event, provider):
        kind = event.get('type')
        if kind in ('permission', 'permission.asked', 'approval_required', 'approval.requested') or event.get('permission_denials'):
            return 'Provider requires a new approval. Execution stopped; inspect retained project changes before continuing.'
        if kind in ('error', 'turn.failed') or (provider == 'claude' and (event.get('is_error') or str(event.get('subtype', '')).startswith('error_'))):
            # Classify internally; never retain arbitrary provider error text or credentials.
            detail = str(event.get('error', event.get('message', ''))).lower()
            if any(word in detail for word in ('auth', 'login', 'sign in', 'credential', 'api key', 'api_key')):
                return 'Provider authentication failed. Sign in with its own CLI; execution stopped and project changes are retained.'
            if any(word in detail for word in ('approval', 'permission')):
                return 'Provider requires a new approval. Execution stopped; inspect retained project changes before continuing.'
            return 'Provider reported an error. Execution stopped; inspect retained project changes before continuing.'
        response = event.get('structured_output') if provider == 'claude' and kind == 'result' else None
        if provider == 'codex' and kind == 'item.completed':
            item = event.get('item')
            if isinstance(item, dict) and item.get('type') == 'agent_message':
                try:
                    response = json.loads(item.get('text', ''))
                except (ValueError, TypeError):
                    pass
        if isinstance(response, dict) and response.get('status') == 'needs_attention':
            return 'Chief reported a limitation requiring attention. Execution stopped; inspect retained project changes.'
        return None

    def _run_one(self, job, cancel, emit, resume=False, lock_project=True):
        try:
            lease = self._lease(job['path']) if lock_project else None
        except RuntimeAttention as exc:
            return {'status': 'needs_attention', 'message': str(exc)}
        process, private_server = None, None
        run_started = time.monotonic()
        try:
            kwargs = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
            environment = dict(cli_environment(self.commands[job['id']], project=job['path']), CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH='1',
                               CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS='1',
                               CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS='1')
            environment.pop('CLAUDE_CODE_SUBAGENT_MODEL_FORCE', None)
            environment.pop('CLAUDE_CODE_SUBAGENT_MODEL', None)
            if job['provider'] == 'opencode':
                environment.update(self._opencode_environment(job))
                private_server, address = self._opencode_server(job, environment, cancel)
                job = dict(job, _server_url=address)
            process = subprocess.Popen(self._command(job, resume), cwd=job['path'], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       env=environment, **kwargs)
            chunks = queue.Queue(maxsize=32)
            reader_stop = threading.Event()
            def read():
                try:
                    while not reader_stop.is_set():
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        while not reader_stop.is_set():
                            try:
                                chunks.put(chunk, timeout=.1)
                                break
                            except queue.Full:
                                continue
                finally:
                    while not reader_stop.is_set():
                        try:
                            chunks.put(None, timeout=.1)
                            break
                        except queue.Full:
                            continue
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            started, count, buffer, final, response_text = run_started, 0, b'', None, ''
            reason, attention_message, reached_eof = None, None, False
            try:
                while True:
                    if cancel.is_set():
                        reason = 'cancelled'
                        break
                    if time.monotonic() - started > self.max_seconds:
                        reason = 'deadline'
                        break
                    try:
                        chunk = chunks.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if chunk is None:
                        reached_eof = True
                        break
                    count += len(chunk)
                    if count > self.max_output:
                        reason = 'output_limit'
                        break
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        kind = event.get('type', '')
                        attention_message = self._attention_message(event, job['provider'])
                        if attention_message:
                            final, reason = 'needs_attention', 'provider_attention'
                            emit({'type': 'needs_attention', 'message': attention_message})
                            break
                        if job['provider'] == 'opencode':
                            part = event.get('part', {})
                            if kind == 'text' and isinstance(part, dict) and isinstance(part.get('text'), str):
                                response_text = (response_text + part['text'])[:16000]
                                try:
                                    response = json.loads(response_text)
                                except ValueError:
                                    response = None
                                if isinstance(response, dict) and response.get('status') == 'needs_attention':
                                    attention_message = 'Worker reported a limitation requiring attention. Execution stopped; inspect retained project changes.'
                                    final, reason = 'needs_attention', 'provider_attention'
                                    emit({'type': 'needs_attention', 'message': attention_message})
                                    break
                            if kind in ('error', 'permission', 'permission.asked'):
                                final = 'needs_attention'
                            emit({'type': 'activity', 'activity': kind if kind in ('text', 'step_start', 'step_finish', 'tool_use') else 'provider_event'})
                            continue
                        if job['provider'] == 'claude':
                            sid = event.get('session_id')
                            if sid and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(sid)):
                                emit({'type': 'session', 'session_id': sid})
                            if kind == 'result':
                                response = event.get('structured_output', {})
                                if isinstance(response, dict) and response.get('status') in ('completed', 'needs_attention'):
                                    final = response['status']
                                if event.get('is_error') or event.get('permission_denials'):
                                    final = 'needs_attention'
                                usage = event.get('usage', {})
                                if isinstance(usage, dict):
                                    emit({'type': 'turn_completed', 'usage': {k: v for k, v in usage.items() if k in ('input_tokens', 'output_tokens', 'cache_read_input_tokens') and isinstance(v, int) and v >= 0}})
                            continue
                        if kind == 'thread.started' and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(event.get('thread_id', ''))):
                            emit({'type': 'session', 'session_id': event['thread_id']})
                        elif kind == 'turn.completed':
                            usage = event.get('usage', {})
                            clean = {k: v for k, v in usage.items() if k in ('input_tokens', 'output_tokens', 'cached_input_tokens') and isinstance(v, int) and v >= 0}
                            emit({'type': 'turn_completed', 'usage': clean})
                        elif kind in ('error', 'turn.failed'):
                            final = 'needs_attention'
                        elif kind == 'item.completed':
                            item = event.get('item', {})
                            if item.get('type') == 'agent_message':
                                try:
                                    response = json.loads(item.get('text', ''))
                                    if response.get('status') in ('completed', 'needs_attention'):
                                        final = response['status']
                                except ValueError:
                                    pass
                            emit({'type': 'activity', 'activity': item.get('type', 'unknown') if item.get('type') in ('agent_message', 'command_execution', 'file_change', 'mcp_tool_call', 'collab_tool_call') else 'provider_event'})
                    if reason:
                        break
            finally:
                reader_stop.set()
                if reached_eof and reason is None:
                    # EOF may precede process exit. Preserve the provider's natural
                    # exit code while keeping shutdown, cancellation and job time bounded.
                    exit_deadline = min(started + self.max_seconds, time.monotonic() + 2)
                    while process.poll() is None:
                        if cancel.is_set():
                            reason = 'cancelled'
                            break
                        remaining = exit_deadline - time.monotonic()
                        if remaining <= 0:
                            reason = 'deadline' if time.monotonic() >= started + self.max_seconds else 'shutdown_limit'
                            break
                        try:
                            process.wait(timeout=min(.1, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                self._stop(process)
                process.stdout.close()
                reader.join(timeout=1)
            if reason:
                return {'status': 'cancelled' if reason == 'cancelled' else 'needs_attention',
                        'message': {'cancelled': 'Cancelled; existing file changes are retained.', 'deadline': 'Time limit reached. Inspect project changes before resuming.', 'output_limit': 'Output limit reached. Inspect project changes before resuming.', 'provider_attention': attention_message, 'shutdown_limit': 'Provider did not exit after closing its output. Execution stopped; inspect retained project changes.'}[reason]}
            if job['provider'] == 'opencode' and process.returncode == 0 and final != 'needs_attention':
                try:
                    response = json.loads(response_text)
                    if isinstance(response, dict):
                        return {'status': 'completed', '_response': response, '_output_bytes': count}
                except ValueError:
                    pass
            if process.returncode or final != 'completed':
                return {'status': 'needs_attention', 'message': 'Provider requires attention. Check CLI login, approvals and project changes; no automatic retry was made.'}
            return {'status': 'completed', 'message': 'Chief reported completion. Inspect project changes and verification evidence.'}
        finally:
            if process is not None:
                self._stop(process)
            if private_server is not None:
                try:
                    private_server.stdin.close()
                    private_server.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    self._stop(private_server)
                finally:
                    private_server.stdout.close()
            if lease is not None:
                lease.close()
