"""Hub operations and bound previews over provider adapters."""
import copy
from pathlib import Path
from datetime import datetime, timezone
import os
import tempfile
from .usage_baseline import evidence, instant
from contextlib import contextmanager
import threading
import time
import uuid
from .storage import PROVIDERS, StateStore
from .discovery import canonical, discover, project, pick_directory
from .runtime import Runtime

class Hub:
    def __init__(self, state_dir=None, adapters=None, jobs=None):
        self.store = StateStore(state_dir)
        from .protocol import WorkProtocol
        self.works = WorkProtocol(self.store.directory / 'works')
        self.installations = StateStore(self.store.directory / 'usage-baselines')
        if adapters is None:
            from .adapters import AdapterManager
            adapters = AdapterManager(usage_cache_dir=self.store.directory / 'usage-index')
        self.adapters = adapters
        self.jobs = jobs
        self.previews = {}
        self.lock = threading.RLock()
        self.path_locks = {}
        self.picker_lock = threading.Lock()
    def close(self):
        if self.jobs and hasattr(self.jobs, 'close'):
            self.jobs.close()
        self.adapters.close()
    def bootstrap(self):
        data = self.store.read()
        data['providers'] = list(PROVIDERS)
        data['capabilities'] = {'native_picker': {'endpoint': '/api/projects/pick'}, 'jobs': self.jobs is not None, 'works': {'schema_version': 1, 'endpoint': '/api/works', 'source': 'local_protocol'}, 'providers': {provider: {'max_concurrency': limit, 'jobs': Runtime.job_capabilities(provider)} for provider, limit in (('codex', 10), ('claude', 20), ('opencode', 1), ('antigravity', 1))}}
        return data
    def provider(self, value):
        if value not in PROVIDERS:
            raise ValueError('Unknown provider')
        return value
    def targets(self, ids, validate_paths=True):
        if not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(x, str) for x in ids):
            raise ValueError('Select between 1 and 100 projects')
        registry = {p['id']: p for p in self.store.read()['projects']}
        result = []
        for ident in dict.fromkeys(ids):
            if ident not in registry:
                raise ValueError('Unknown registered project')
            entry = registry[ident]
            if validate_paths:
                try:
                    current = canonical(entry['path'])
                except OSError as error:
                    raise ValueError('Project folder is unavailable; refresh the registry') from error
                if current != entry['path']:
                    raise ValueError('Project path changed; refresh registry')
            result.append(entry)
        return result
    def pick_project_directory(self, body):
        if body not in ({}, {"kind": "directory"}):
            raise ValueError("Folder picker accepts only an empty object or directory kind")
        if not self.picker_lock.acquire(blocking=False):
            raise ValueError("A folder picker is already open")
        try:
            path = pick_directory()
            return {"path": path, "cancelled": path is None}
        finally:
            self.picker_lock.release()
    def projects(self, body):
        action = body.get('action')
        snapshot = self.store.read()
        if action == 'add':
            entry = project(body.get('path'))
            def change(data):
                data['projects'] = [p for p in data['projects'] if p['id'] != entry['id']] + [entry]
        elif action == 'refresh':
            roots = body.get('roots', snapshot['roots'])
            if not isinstance(roots, list) or len(roots) > 20:
                raise ValueError('Select at most 20 discovery roots')
            roots = list(dict.fromkeys(canonical(root) for root in roots))
            entries = discover(roots)
            def change(data):
                # Explicit manual additions remain registered across discovery refreshes.
                manual = [p for p in data['projects'] if p.get('manual')]
                combined = {p['id']: p for p in entries + manual}
                data['roots'], data['projects'] = roots, list(combined.values())
                data['selected_projects'] = [x for x in data['selected_projects'] if x in combined]
        elif action == 'remove':
            ident = body.get('id')
            self.targets([ident])
            def change(data):
                data['projects'] = [p for p in data['projects'] if p['id'] != ident]
                data['selected_projects'] = [x for x in data['selected_projects'] if x != ident]
                data['project_overrides'].pop(ident, None)
        elif action == 'selection':
            ids = body.get('ids', [])
            if ids:
                self.targets(ids)
            elif not isinstance(ids, list):
                raise ValueError('ids must be a list')
            selected = body.get('providers', snapshot['selected_providers'])
            self.validate_providers(selected)
            def change(data):
                data['selected_projects'] = list(dict.fromkeys(ids))
                data['selected_providers'] = selected
        else:
            raise ValueError('Unknown project action')
        if action == 'add':
            entry['manual'] = True
        return self.store.update(change, body.get('revision', snapshot['revision']))
    def validate_providers(self, values):
        if not isinstance(values, list) or not values:
            raise ValueError('Select at least one provider')
        for value in values:
            self.provider(value)
    def orchestra(self, value):
        if not isinstance(value, dict):
            raise ValueError('Orchestra must be an object')
        value = copy.deepcopy(value)
        value.setdefault('id', uuid.uuid4().hex)
        if value.get('version', value.get('schema', 1)) != 1:
            raise ValueError('Unsupported orchestra version')
        if 'profile' in value and not isinstance(value['profile'], str):
            raise ValueError('Profile must be a string')
        if not isinstance(value['id'], str) or not value['id'] or not isinstance(value.get('name'), str) or not value['name'].strip():
            raise ValueError('Orchestra id and name are required')
        self.provider(value.get('provider'))
        if not isinstance(value.get('chief'), dict) or not isinstance(value.get('helpers'), list):
            raise ValueError('Chief and helpers are required')
        concurrency = value.get('concurrency')
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= {'codex': 10, 'claude': 20, 'opencode': 1, 'antigravity': 1}[value['provider']]:
            raise ValueError('Concurrency is unsupported by the selected provider')
        helper_ids = set()
        for helper in value['helpers']:
            if not isinstance(helper, dict) or any(not isinstance(helper.get(key), str) or not helper[key] for key in ('id', 'role', 'name', 'model')):
                raise ValueError('Each helper needs id, role, name and model')
            if not isinstance(helper.get('effort'), str):
                raise ValueError('Each helper needs an explicit effort string')
            if helper['id'] in helper_ids:
                raise ValueError('Helper ids must be unique')
            helper_ids.add(helper['id'])
        if not isinstance(value['chief'].get('model'), str) or not value['chief']['model'] or not isinstance(value['chief'].get('effort'), str):
            raise ValueError('Chief needs model and effort')
        if value['provider'] == 'antigravity':
            selections = [value['chief'], *value['helpers']]
            if not 1 <= len(value['helpers']) <= 50 or any(x.get('model') not in ('inherit', 'flash', 'pro') or x.get('effort') != '' for x in selections):
                raise ValueError('Antigravity teams require inherit/flash/pro and empty effort')
            if any(h['role'].replace('-', '_') not in Runtime.ROLES for h in value['helpers']):
                raise ValueError('Unsupported Antigravity role')
        return value
    def orchestras(self, body):
        if body.get('action') == 'save':
            entry = self.orchestra(body.get('orchestra'))
            if entry['provider'] == 'claude':
                self.adapters.validate_team('claude', entry)
            def change(data):
                data['orchestras'] = [p for p in data['orchestras'] if p['id'] != entry['id']] + [entry]
        elif body.get('action') == 'remove':
            ident = body.get('id')
            if not isinstance(ident, str):
                raise ValueError('Orchestra id is required')
            def change(data):
                data['orchestras'] = [p for p in data['orchestras'] if p['id'] != ident]
        else:
            raise ValueError('Unknown orchestra action')
        return self.store.update(change, body.get('revision'))
    def defaults(self, body):
        for key in ('defaults', 'override'):
            if key in body and not isinstance(body[key], dict):
                raise ValueError(key + ' must be an object')
        if 'selected_providers' in body:
            self.validate_providers(body['selected_providers'])
        if 'project_id' in body:
            self.targets([body['project_id']])
        def change(data):
            if 'defaults' in body:
                data['defaults'] = body['defaults']
            if 'selected_providers' in body:
                data['selected_providers'] = body['selected_providers']
            if 'project_id' in body:
                data['project_overrides'][body['project_id']] = body.get('override', {})
        return self.store.update(change, body.get('revision'))
    def payload(self, body):
        payload = body.get('payload', {})
        if not isinstance(payload, dict):
            raise ValueError('payload must be an object')
        if 'orchestra' in payload:
            payload = payload['orchestra']
        if any(key in payload for key in ('chief', 'helpers', 'provider')):
            payload = self.orchestra(payload)
            if payload['provider'] != body['provider']:
                raise ValueError('Cross-provider orchestra requires explicit remapping before preview')
        return copy.deepcopy(payload)
    def _path_lock(self, path):
        with self.lock:
            return self.path_locks.setdefault(path, threading.RLock())
    @contextmanager
    def mutation(self, path):
        # The jobs manager starts under this same lock, preventing a check/start race.
        guard = self.jobs.lock if self.jobs and hasattr(self.jobs, "lock") else self.lock
        with guard:
            if self.jobs and path in getattr(self.jobs, "active", {}):
                raise ValueError("A job is active for this project; stop it before changing configuration")
            yield
    def batch(self, action, body):
        provider = self.provider(body.get('provider'))
        targets = self.targets(body.get('project_ids'), validate_paths=False)
        payload = self.payload(body)
        results = []
        for target in targets:
            result = {'project_id': target['id'], 'ok': True}
            try:
                with self._path_lock(target['path']):
                    # Registered stale paths fail individually; unknown IDs fail the request.
                    self.targets([target['id']])
                    if action == 'preview':
                        revision = self.store.read()['revision']
                        output = self.adapters.preview(provider, target['path'], payload)
                        if not isinstance(output, dict) or not output.get('preview_id'):
                            raise ValueError('Adapter did not return a bound preview')
                        root_identity = output.get('root_identity') if provider == 'antigravity' else None
                        if provider == 'antigravity' and (not isinstance(root_identity, dict) or set(root_identity) != {'device', 'inode'} or any(type(v) is not int for v in root_identity.values()) or root_identity['inode'] <= 0):
                            raise ValueError('Antigravity preview did not bind project directory identity')
                        ident = uuid.uuid4().hex
                        with self.lock:
                            self.previews = {key: item for key, item in self.previews.items() if time.monotonic() - item['created'] <= 300}
                            if len(self.previews) >= 1000:
                                raise ValueError('Too many pending previews; apply or restart the hub')
                            self.previews[ident] = {'project': target, 'provider': provider, 'payload': payload, 'revision': revision, 'adapter_id': output['preview_id'], 'created': time.monotonic(), 'root_identity': root_identity}
                        result.update(preview_id=ident, preview=output)
                    else:
                        if action == 'restore':
                            with self.store.lock, self.mutation(target['path']):
                                self.targets([target['id']])
                                proof, previous, _ = self._installation(target, provider)
                                output = self.adapters.restore(provider, target['path'], payload)
                                result['provider_restore_succeeded'] = True
                                restored = evidence(target['path'], provider)
                                undo = previous.get('undo') if isinstance(previous, dict) else None
                                if undo and restored['installed'] and undo.get('manifest_sha') == restored['manifest_sha']:
                                    entry = undo
                                else:
                                    _, _, entry = self._installation(target, provider)
                                self._record_installation(target, provider, entry)
                        else:
                            output = self.usage(provider, target['id']) if action == 'usage' else getattr(self.adapters, action)(provider, target['path'])
                        result['result'] = output
            except Exception as error:
                result.update(ok=False, error=str(error))
                if getattr(error, 'stage', None) in ('preflight', 'backup', 'applied', 'verified', 'restore'):
                    result['stage'] = error.stage
                if getattr(error, 'recovery_status', None) in ('not_started', 'rolled_back', 'conflicts_retained'):
                    result['recovery_status'] = error.recovery_status
                if result.get('provider_restore_succeeded'):
                    result.update(receipt_status='receipt_failed', error='Provider restore succeeded; usage receipt could not be saved.')
            results.append(result)
        return {'results': results}
    def _installation(self, target, provider):
        proof = evidence(target['path'], provider)
        prior = self.installations.read().get('usage_installations', {}).get(target['id'], {}).get(provider)
        valid_prior = isinstance(prior, dict) and prior.get('active') and instant(prior.get('started_at'))
        continuous = valid_prior and proof['installed'] and prior.get('manifest_sha') in (proof['manifest_sha'], proof['previous_sha'])
        if continuous:
            entry = copy.deepcopy(prior)
            entry['manifest_sha'] = proof['manifest_sha']
        elif proof['installed'] and proof['started_at']:
            entry = {'active': True, 'started_at': proof['started_at'], 'manifest_sha': proof['manifest_sha'], 'generation': uuid.uuid4().hex, 'adopted': True}
        else:
            entry = {'active': False}
        return proof, prior, entry
    def _record_installation(self, target, provider, entry):
        def change(data):
            data.setdefault('usage_installations', {}).setdefault(target['id'], {})[provider] = entry
        self.installations.update(change)
    @contextmanager
    def _usage_guard(self, path):
        lock = self._path_lock(path)
        if not lock.acquire(timeout=45):
            raise ValueError('Usage collection is already running; retry shortly')
        try:
            yield
        finally:
            lock.release()
    def usage(self, provider, ident):
        target = self.targets([ident])[0]
        with self._usage_guard(target['path']):
            with self.installations.lock:
                proof, prior, entry = self._installation(target, provider)
                if entry != prior and (entry.get('active') or prior):
                    self._record_installation(target, provider, entry)
                boundary = {'status': 'verified' if entry.get('active') else 'unverified', 'started_at': entry.get('started_at'), 'conservative': bool(entry.get('adopted'))}
            if provider == 'antigravity':
                return {'provider': provider, 'status': 'unsupported', 'source': 'none', 'installation_boundary': boundary, 'totals': {}, 'records': [], 'limitations': ['No verified trusted project-scoped Antigravity CLI usage record source.']}
            if not entry.get('active'):
                return {'status': 'unavailable', 'source': 'none', 'installation_boundary': boundary, 'totals': {}, 'records': []}
            output = self.adapters.usage(provider, target['path'])
            if evidence(target['path'], provider)['manifest_sha'] != proof['manifest_sha']:
                raise ValueError('Installation changed while collecting usage; reload usage')
            boundary['observed_at'] = datetime.now(timezone.utc).isoformat()
            return dict(output, installation_boundary=boundary)

    def apply(self, body):
        ids = body.get('preview_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(x, str) for x in ids):
            raise ValueError('Select valid preview ids')
        results = []
        for ident in ids:
            with self.lock:
                bound = self.previews.pop(ident, None)
            result = {'preview_id': ident, 'ok': True}
            try:
                if not bound:
                    raise ValueError('Unknown or already consumed preview')
                target = bound['project']
                result['project_id'] = target['id']
                with self._path_lock(target['path']), self.store.lock:
                    current = self.targets([target['id']])[0]
                    if current['path'] != target['path'] or bound['revision'] != self.store.read()['revision'] or time.monotonic() - bound['created'] > 300:
                        raise ValueError('Preview expired or metadata changed; preview again')
                    if bound.get('root_identity') is not None:
                        info = Path(target['path']).stat(follow_symlinks=False)
                        if bound['root_identity'] != {'device': info.st_dev, 'inode': info.st_ino}:
                            raise ValueError('Project directory identity changed since preview; no mutation permitted')
                    with self.mutation(target['path']), self.installations.lock:
                        provider = bound['provider']
                        _, prior, before = self._installation(target, provider)
                        # Check private metadata write readiness before touching provider files.
                        fd, probe = tempfile.mkstemp(prefix='.usage-receipt-', dir=self.installations.directory)
                        os.close(fd); os.unlink(probe)
                        result['result'] = self.adapters.apply(provider, target['path'], bound['adapter_id'])
                        result['provider_apply_succeeded'] = True
                        completed = datetime.now(timezone.utc).isoformat()
                        proof = evidence(target['path'], provider)
                        if proof['installed']:
                            entry = copy.deepcopy(before) if before.get('active') else {'active': True, 'started_at': completed, 'generation': uuid.uuid4().hex, 'adopted': False}
                            entry['manifest_sha'] = proof['manifest_sha']
                            if before.get('active'):
                                entry['undo'] = {k: v for k, v in before.items() if k != 'undo'}
                            self._record_installation(target, provider, entry)
                            result['receipt_status'] = 'recorded'
                        else:
                            result['receipt_status'] = 'installation_unverified'
            except Exception as error:
                result.update(ok=False, error=str(error))
                if getattr(error, 'stage', None) in ('preflight', 'backup', 'applied', 'verified', 'restore'):
                    result['stage'] = error.stage
                if getattr(error, 'recovery_status', None) in ('not_started', 'rolled_back', 'conflicts_retained'):
                    result['recovery_status'] = error.recovery_status
                if result.get('provider_apply_succeeded'):
                    result['receipt_status'] = 'receipt_failed'
                    result['error'] = 'Provider apply succeeded; usage receipt could not be saved.'
            results.append(result)
        return {'results': results}
    def job_action(self, body):
        if not self.jobs:
            raise ValueError('Job manager unavailable')
        action = body.get('action')
        if action == 'plan':
            payload = copy.deepcopy(body)
            targets = self.targets(body.get('project_ids'))
            self.provider(body.get('provider'))
            payload['targets'] = [{'project_id': p['id'], 'path': p['path']} for p in targets]
            # Never pass caller-supplied paths through to jobs.
            for key in ('path', 'target', 'paths'):
                payload.pop(key, None)
            return {'job': self.jobs.plan(payload)}
        if action in ('start', 'cancel', 'resume'):
            ident = body.get('id', body.get('job_id'))
            if not isinstance(ident, str):
                raise ValueError('Job id required')
            if action in ('start', 'resume'):
                # Lock order is metadata then jobs, including Runtime.prepare.
                with self.store.lock:
                    job = self.jobs.get(ident)
                    registered = self.targets([job['project_id']])[0]
                    if registered['path'] != job['path']:
                        raise ValueError('Job project identity changed; create a new plan')
                    return {'job': getattr(self.jobs, action)(ident)}
            return {'job': self.jobs.cancel(ident)}
        raise ValueError('Unknown job action')

    def work_list(self):
        registered = [p['id'] for p in self.store.read()['projects']]
        return {'works': self.works.list(registered), 'schema_version': 1}

    def work_action(self, body):
        from .protocol import ProtocolError
        if not isinstance(body, dict):
            raise ProtocolError('Work request must be an object')
        action = body.get('action')
        if action not in ('approve', 'answer', 'apply', 'cancel', 'resume'):
            raise ProtocolError('Native tool publishes work; UI controls approval and recovery only')
        with self.store.lock:
            records = self.works.list()
            work = next((w for w in records if w['id'] == body.get('id')), None)
            if not work:
                raise ProtocolError('Published work not found')
            registered = self.targets([work['project_id']])[0]
            if registered['path'] != work['path']:
                raise ProtocolError('Work project identity changed; publish a new contract')
            payload = {k: v for k, v in body.items() if k not in ('action', 'operation_id')}
            return {'work': self.works.mutate(action, payload, body.get('operation_id'), trusted=True)}
