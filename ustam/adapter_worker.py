"""Worker entry point. Only hash-verified bundled modules may be imported."""
from __future__ import annotations
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import tomllib

MAX_MESSAGE = 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
BASE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
PROVIDERS = ('codex', 'claude', 'opencode', 'antigravity')
METHODS = {'inspect', 'preview', 'apply', 'restore', 'usage', 'models', 'validate_team'}

def verify_engine(provider):
    manifest = json.loads((BASE / 'engine-manifest.json').read_text())
    engine = BASE / 'engines' / provider
    expected = manifest['engines'][provider]['files']
    actual = {p.relative_to(engine).as_posix() for p in engine.rglob('*') if p.is_file()}
    if actual != set(expected):
        raise ValueError('Engine asset closure mismatch')
    for relative, digest in expected.items():
        path = engine / relative
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != BASE):
            raise ValueError('Engine symlink refused')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Engine asset integrity failure')
    return engine

class Engine:
    def __init__(self, provider):
        self.provider = provider
        self.root = verify_engine(provider)
        os.chdir(self.root)
        # Search trusted snapshot first; drop working/project paths completely.
        if getattr(sys, 'frozen', False):
            # PyInstaller's archive and native extensions live under _MEIPASS.
            # Keep only that trusted runtime, never an external/project path.
            runtime = Path(sys._MEIPASS).resolve()
            standard = [p for p in sys.path if p and Path(p).resolve().is_relative_to(runtime)]
        else:
            standard = [p for p in sys.path if p and not Path(p).resolve().is_relative_to(BASE.parent)]
        tools = self.root / ('.claude/tools' if provider == 'claude' else '.opencode/tools')
        sys.path[:] = [str(self.root / 'scripts'), *([str(tools)] if provider != 'codex' else []), *standard]
        self.installer = importlib.import_module('install')
        self.module = importlib.import_module({'codex': 'dashboard', 'claude': 'console_settings', 'opencode': 'console', 'antigravity': 'console_settings'}[provider])
        self.backend, self.target, self.pending = None, None, None

    def select(self, target):
        path = Path(target).resolve(strict=True)
        if not path.is_dir() or path.is_relative_to(BASE.parent):
            raise ValueError('Project directory required outside hub distribution')
        if self.target is not None and path != self.target:
            raise ValueError('Worker is bound to one canonical project')
        if self.backend is None:
            if self.provider == 'antigravity':
                if getattr(sys, 'frozen', False):
                    command = [sys.executable, '--work-protocol', 'antigravity']
                else:
                    code = 'import sys; sys.path.insert(0, ' + repr(str(BASE.parent)) + '); from ustam.__main__ import main; raise SystemExit(main())'
                    command = [sys.executable, '-I', '-c', code, '--work-protocol', 'antigravity']
                self.backend = self.module.Settings(path, bridge_command=command)
            elif self.provider == 'codex':
                self.backend = self.module.Console(path, Path.home() / '.codex/sessions')
            elif self.provider == 'claude':
                self.backend = self.module.Settings(self.installer, self.root, path)
            else:
                self.backend = self.module.Settings(path)
            self.target = path

    def native_state(self):
        if self.provider == 'codex': return self.backend.settings()
        if self.provider == 'claude': return self.backend.read()
        # Inspection never probes installed project code.
        path = self.target / '.opencode/.bounded-orchestrator/install.json'
        self.module.safe(path)
        _, manifest = self.module.read(path)
        if manifest and (manifest.get('schema') != 1 or not isinstance(manifest.get('team', []), list)):
            raise ValueError('Invalid installed team state')
        config_path = self.backend.target('project')
        _, config = self.module.read(config_path)
        return {'installed': bool(manifest), 'team': manifest.get('team', []), 'model': config.get('model', ''),
                'profile': self.backend.profile_from_config(config_path.read_text()) if manifest else 'balanced',
                'restore_available': False, 'limitations': ['Roster undo is not exposed by this backend.', 'Concurrency has no verified OpenCode setting; one active helper is supported.', 'Bundled schema was verified with OpenCode 2.0.3 in repository runtime smoke tests; compatibility with another installed version requires runtime inspection.']}

    def inspect(self):
        state = self.native_state()
        p = self.provider
        roles = state.get('roles', state.get('routing', {}))
        chief = roles.get('owner', {'model': state.get('model', ''), 'effort': ''})
        native_helpers = state.get('team', []) if p != 'claude' else state.get('roster', [])
        helpers = []
        for index, item in enumerate(native_helpers, 1):
            model, effort = item.get('model', ''), item.get('effort', '')
            if p == 'opencode' and '#' in model:
                model, effort = model.rsplit('#', 1)
            helpers.append({'id': f'helper-{index:02d}', 'role': item.get('duty' if p == 'codex' else 'role', '').replace('_', '-'),
                            'name': item.get('title', item.get('label', item.get('duty', ''))), 'model': model, 'effort': effort})
        return {'provider': p, 'target': str(self.target), 'installed': bool(state['installed']), 'chief': chief,
                'helpers': helpers, 'concurrency': state.get('concurrency', state.get('max_parallelism')) or 1,
                'profile': state.get('saved_preset', state.get('preset', state.get('profile'))) or 'balanced',
                'restore_available': bool(state.get('restore_available')), 'limitations': state.get('limitations', []),
                'capabilities': {'preview': True, 'apply': True, 'restore': p != 'opencode', 'max_concurrency': {'codex': 10, 'claude': 20, 'opencode': 1}[p], 'runtime_compatibility': 'unverified'}}

    def catalog(self):
        if self.provider == 'codex': return dict(self.backend.models(), provider='codex')
        if self.provider == 'opencode':
            catalog = self.module.model_catalog(self.target)
            catalog['models'] = [{'id': value, 'efforts': [], 'origin': 'local_cli'} for value in catalog.get('models', [])]
            catalog['provider'] = 'opencode'
            return catalog
        # Claude backend validates aliases/IDs syntactically; it exposes no entitlement discovery.
        return {'provider': 'claude', 'status': 'unverified', 'source': 'documented pinned models and unresolved native aliases',
                'models': [self.installer.model_capabilities.capabilities(value) for value in self.installer.model_capabilities.CATALOG_MODELS],
                'limitations': ['Claude account access is unverified. Explicit effort requires a documented pinned model; unresolved aliases and unsupported models use default effort with no native effort field.', 'Chief effort max is unsupported.']}

    def models(self):
        catalog = self.catalog()
        roles = self.installer.ROLE_FILES if self.provider == 'codex' else (self.installer.ROLES[1:] if self.provider == 'claude' else self.module.team_editor.ROLES)
        catalog['roles'] = [role.replace('_', '-') for role in roles]
        catalog['efforts'] = list(self.installer.EFFORTS) if self.provider == 'codex' else (sorted(self.installer.CLAUDE_EFFORTS) if self.provider == 'claude' else [])
        catalog['chief_efforts'] = list(self.installer.EFFORTS) if self.provider == 'codex' else (sorted(self.installer.CLAUDE_OWNER_EFFORTS) if self.provider == 'claude' else [])
        catalog['max_concurrency'] = {'codex': 10, 'claude': 20, 'opencode': 1}[self.provider]
        catalog['profiles'] = list(self.installer.PRESETS) if self.provider != 'opencode' else list(self.module.team_editor.PROFILES)
        catalog['profile_recommendations'] = self.profile_recommendations(catalog)
        catalog['effort_semantics'] = 'model variant suffix; discovered model list does not enumerate variants' if self.provider == 'opencode' else 'reasoning effort'
        return catalog

    def profile_recommendations(self, catalog):
        # These are the shipped profile settings, not measured performance claims.
        known = {item['id'] for item in catalog['models']}
        recommendations = {}
        if self.provider == 'opencode':
            for profile, steps in self.installer.PROFILES.items():
                recommendations[profile] = {'chief': None, 'role_models': {}, 'role_efforts': {},
                                            'concurrency': 1, 'role_steps': dict(steps),
                                            'source': 'pinned backend step profile', 'access_verified': False,
                                            'limitations': ['This backend profile chooses step counts, not models or reasoning variants.']}
            return recommendations
        if self.provider == 'codex':
            config = tomllib.loads((self.root / '.codex/config.toml').read_text())
            concurrency = config.get('agents', {}).get('max_concurrent_threads_per_session')
        else:
            config = json.loads((self.root / '.claude/settings.json').read_text())
            value = config.get('env', {}).get(self.module.CONCURRENCY)
            concurrency = int(value) if isinstance(value, str) and value.isdigit() else None
        for profile, choices in self.installer.PRESETS.items():
            owner_model, owner_effort = choices['owner']
            permitted = {role.replace('_', '-'): (model, effort) for role, (model, effort) in choices.items() if role != 'owner' and model in known}
            recommendations[profile] = {'chief': {'model': owner_model, 'effort': owner_effort} if owner_model in known else None,
                                        'role_models': {role: pair[0] for role, pair in permitted.items()},
                                        'role_efforts': {role: pair[1] for role, pair in permitted.items()},
                                        'concurrency': concurrency, 'source': 'pinned backend model/effort profile',
                                        'access_verified': False,
                                        'limitations': ['Profile recommendations do not verify account access.', 'Null concurrency means the shipped profile does not set a numeric limit.']}
        return recommendations

    def map_payload(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'id', 'name', 'task_type', 'version', 'schema', 'provider', 'chief', 'helpers', 'concurrency', 'profile', 'replace', 'allow_mixed'}:
            raise ValueError('Unknown orchestra fields')
        if payload.get('provider', self.provider) != self.provider or payload.get('version', payload.get('schema', 1)) != 1:
            raise ValueError('Orchestra provider/version mismatch')
        if 'task_type' in payload and (not isinstance(payload['task_type'], str) or len(payload['task_type']) > 100):
            raise ValueError('Invalid task type description')
        chief, helpers = payload.get('chief'), payload.get('helpers')
        if self.provider == 'claude':
            self.validate_team({'chief': chief, 'helpers': helpers})
        if not isinstance(chief, dict) or set(chief) != {'model', 'effort'} or not all(isinstance(v, str) for v in chief.values()):
            raise ValueError('Chief model and effort required')
        if not isinstance(helpers, list) or not 1 <= len(helpers) <= 50:
            raise ValueError('Choose 1 to 50 helpers')
        for index, item in enumerate(helpers, 1):
            if not isinstance(item, dict) or set(item) != {'id', 'role', 'name', 'model', 'effort'} or not all(isinstance(v, str) for v in item.values()):
                raise ValueError('Invalid helper')
            if len(item['id']) > 100 or len(item['name']) > 48:
                raise ValueError('Helper identity/name too long')
        if len({h['id'] for h in helpers}) != len(helpers):
            raise ValueError('Duplicate helper identity')
        cap = payload.get('concurrency', 1)
        if type(cap) is not int or not 1 <= cap <= {'codex': 10, 'claude': 20, 'opencode': 1}[self.provider]:
            raise ValueError('Concurrency is unsupported by this backend')
        state = self.native_state()
        if self.provider == 'codex':
            preset = payload.get('profile', 'balanced')
            if preset not in self.installer.PRESETS: raise ValueError('Unsupported Codex profile')
            roles = {role: {'model': model, 'effort': effort} for role, (model, effort) in self.installer.PRESETS[preset].items()}
            roles['owner'] = chief
            team = [{'slot': f'team_slot_{i:02d}', 'duty': h['role'].replace('-', '_'), 'title': h['name'], 'model': h['model'], 'effort': h['effort']} for i, h in enumerate(helpers, 1)]
            return {'preset': preset, 'roles': roles, 'team': team, 'team_count': len(team), 'concurrency': cap}
        if self.provider == 'claude':
            preset = payload.get('profile', 'balanced')
            if preset not in {*self.installer.PRESETS, 'custom'}: raise ValueError('Unsupported Claude profile')
            roles = dict(state['routing']) if preset == 'custom' else {role: {'model': m, 'effort': e} for role, (m, e) in self.installer.PRESETS[preset].items()}
            roles['owner'] = chief
            return {'preset': preset, 'routing': roles, 'max_parallelism': cap,
                    'roster': [{'id': f'slot-{i:02d}', 'role': h['role'], 'label': h['name'], 'model': h['model'], 'effort': h['effort']} for i, h in enumerate(helpers, 1)]}
        if chief['effort']: raise ValueError('OpenCode chief effort has no verified backend mapping')
        team = [{'role': h['role'], 'model': h['model'] + ('#' + h['effort'] if h['effort'] else ''), 'duty': h['name']} for h in helpers]
        return {'project': str(self.target), 'profile': payload.get('profile', 'balanced'), 'model': chief['model'], 'team': team,
                'replace': payload.get('replace') is True, 'allow_mixed': payload.get('allow_mixed') is True,
                'revision': self.module.team_revision(self.target)}

    def validate_team(self, params):
        if self.provider != 'claude':
            raise ValueError('Pure team validation is only exposed for Claude')
        if not isinstance(params, dict) or set(params) != {'chief', 'helpers'} or not isinstance(params['chief'], dict) or not isinstance(params['helpers'], list):
            raise ValueError('Claude chief and helpers are required')
        for index, choice in enumerate([params['chief'], *params['helpers']]):
            if not isinstance(choice, dict) or not isinstance(choice.get('model'), str) or not isinstance(choice.get('effort'), str):
                raise ValueError('Claude model and effort must be text')
            self.installer.validate_claude_model(choice['model'], 'model')
            self.installer.validate_model_effort(choice['model'], choice['effort'], 'chief' if index == 0 else 'helper', chief=index == 0)
        return {'provider': 'claude', 'valid': True}

    def call(self, method, target, params):
        if method == 'validate_team':
            return self.validate_team(params)
        self.select(target)
        if self.provider == 'antigravity':
            return self.backend.call(method, params)
        if method == 'inspect': return self.inspect()
        if method == 'models': return self.models()
        if method == 'usage':
            if self.provider == 'codex': return self.backend.report({'project': [str(self.target)]})
            usage = importlib.import_module('usage_report')
            if self.provider == 'claude': return usage.report(None, history=self.backend.usage_history(), project_hash=self.backend.project_hash())
            return usage.collect_breakdown(root=self.target, project=str(self.target))
        if method == 'preview':
            native = self.map_payload(params)
            if self.provider == 'codex': result = self.backend.preview(native)
            elif self.provider == 'claude': result = self.backend.plan(native)[2]
            else: result = self.module.team_request(self.backend, self.installer, native, 'preview')
            token = secrets.token_urlsafe(24)
            self.pending = (token, time.monotonic() + 300, native, result)
            return dict(result, preview_id=token, provider=self.provider, target=str(self.target))
        if method == 'apply':
            pending, self.pending = self.pending, None
            if set(params) != {'preview_id'} or not pending or params['preview_id'] != pending[0] or time.monotonic() > pending[1]:
                raise ValueError('Fresh preview required')
            _, _, native, result = pending
            if self.provider == 'codex': return self.backend.save({'preview_id': result['preview_id']})
            if self.provider == 'claude': return self.backend.save(dict(native, revision=result['revision']))
            return self.module.team_request(self.backend, self.installer, native, 'save')
        if method == 'restore':
            if params: raise ValueError('Restore takes no fields')
            self.pending = None
            if self.provider == 'codex': return self.backend.restore({})
            if self.provider == 'claude': return self.backend.restore()
            raise ValueError('OpenCode backend does not expose safe roster restore')
        raise ValueError('Unknown adapter method')

def scrub(message):
    return re.sub(r'(?i)(sk-[A-Za-z0-9_-]+|(?:api[_-]?key|token|password)\s*[:=]\s*[^\s,;]+)', '[redacted]', message)[:2000]

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in PROVIDERS:
        return 2
    try:
        engine = Engine(argv[0])
    except Exception:
        print('Adapter engine initialization failed', file=sys.stderr)
        return 2
    for raw in iter(lambda: sys.stdin.buffer.readline(MAX_MESSAGE + 1), b''):
        reqid = None
        try:
            if len(raw) > MAX_MESSAGE or not raw.endswith(b'\n'): return 2
            message = json.loads(raw)
            if not isinstance(message, dict) or set(message) != {'reqid', 'method', 'target', 'params'}:
                raise ValueError('Invalid adapter message')
            reqid = message['reqid']
            if not isinstance(reqid, str) or not re.fullmatch(r'[a-f0-9]{32}', reqid): raise ValueError('Invalid request identity')
            if message['method'] not in METHODS or not isinstance(message['target'], str) or len(message['target']) > 4096 or not isinstance(message['params'], dict):
                raise ValueError('Invalid request fields')
            # Backend installers print diagnostics. Keep the JSON transport pristine.
            with contextlib.redirect_stdout(io.StringIO()):
                result = engine.call(message['method'], message['target'], message['params'])
            response = {'reqid': reqid, 'ok': True, 'result': result}
        except Exception as exc:
            timeout_type = getattr(getattr(engine.module, 'usage', None), 'UsageIndexTimeout', ())
            code = 'usage_index_timeout' if engine.provider == 'codex' and isinstance(exc, timeout_type) else 'adapter_error'
            response = {'reqid': reqid, 'ok': False, 'error': {'code': code, 'message': scrub(str(exc))}}
            if getattr(exc, 'stage', None) in ('preflight', 'backup', 'applied', 'verified', 'restore'):
                response['error']['stage'] = exc.stage
            if getattr(exc, 'recovery_status', None) in ('not_started', 'rolled_back', 'conflicts_retained'):
                response['error']['recovery_status'] = exc.recovery_status
        data = (json.dumps(response, allow_nan=False) + '\n').encode()
        if len(data) > MAX_RESPONSE:
            data = (json.dumps({'reqid': reqid, 'ok': False, 'error': {'code': 'bounds', 'message': 'Adapter result exceeded bounds'}}) + '\n').encode()
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    return 0
