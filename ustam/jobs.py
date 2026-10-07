"""Private job metadata and one active writer per canonical project.

Only the hub may resolve registered project IDs into the targets passed to plan.
The proposed plan is deterministic; creating it never invokes a paid model.
"""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from .runtime import Runtime, RuntimeAttention
from .storage import process_lock


class JobManager:
    def __init__(self, state_dir, adapters=None, runtime=None):
        self.directory = Path(state_dir) / 'jobs'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'jobs.json'
        self.runtime = runtime or Runtime(self.directory)
        self.lock = process_lock(self.directory / '.jobs.lock')
        self._active = {}
        self.threads = {}
        self.closed = False
        self.owner_id = uuid.uuid4().hex
        with self.lock:
            self._reload()

    @staticmethod
    def _owner_alive(job):
        pid = job.get('owner_pid')
        if not isinstance(pid, int) or pid <= 0:
            return None  # Old or unknown owners cannot safely be declared interrupted.
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False if ctypes.get_last_error() == 87 else None
            try:
                code = wintypes.DWORD()
                return (code.value == 259) if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return None
        return True

    def _reload(self):
        self.jobs = json.loads(self.path.read_text()) if self.path.exists() else {}
        changed = False
        for job in self.jobs.values():
            if job['status'] in ('running', 'cancelling') and self._owner_alive(job) is False:
                job.update(status='needs_attention', message='Owning hub process interrupted. Inspect project changes before resuming.')
                changed = True
        if changed:
            self._save()

    @property
    def active(self):
        with self.lock:
            self._reload()
            active = {job['path']: job['id'] for job in self.jobs.values() if job['status'] in ('running', 'cancelling')}
            active.update(self._active)
            return active

    def _save(self):
        fd, name = tempfile.mkstemp(prefix='.jobs-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(self.jobs, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def list(self):
        with self.lock:
            self._reload()
            return copy.deepcopy(list(self.jobs.values()))

    def get(self, identifier):
        with self.lock:
            self._reload()
            if identifier not in self.jobs:
                raise ValueError('Job not found')
            return copy.deepcopy(self.jobs[identifier])

    def plan(self, payload):
        with self.lock:
            self._reload()
            if self.closed:
                raise ValueError('Job manager is closed')
            targets = payload.get('targets')
            if not isinstance(targets, list) or len(targets) != 1:
                raise ValueError('Plan one registered project at a time')
            target = targets[0]
            if not isinstance(target, dict) or not target.get('project_id'):
                raise ValueError('Registered project ID is required')
            path = str(Path(target.get('path', '')).resolve(strict=True))
            if not Path(path).is_dir():
                raise ValueError('Project directory is required')
            task = payload.get('task', '')
            if not isinstance(task, str) or not task.strip() or len(task) > 16000:
                raise ValueError('Task must contain 1–16000 characters')
            orchestra = payload.get('orchestra')
            if not isinstance(orchestra, dict):
                raise ValueError('Selected orchestra is required')
            provider = orchestra.get('provider', payload.get('provider'))
            if provider == 'antigravity':
                raise ValueError('Antigravity unattended Jobs are unsupported; use the manual Works bridge.')
            if provider not in ('codex', 'claude', 'opencode'):
                raise ValueError('Unsupported provider')
            if payload.get('provider', provider) != provider:
                raise ValueError('Provider and orchestra must match')
            # Retain selected role/model/effort fields, never arbitrary configuration.
            chief = self._selection(orchestra.get('chief', {}))
            capabilities = Runtime.job_capabilities(provider)
            helpers = []
            for item in orchestra.get('helpers', []):
                role = item.get('role', '').replace('-', '_')
                if role.replace('_', '-') not in capabilities['supported_roles']:
                    raise ValueError('Unsupported helper role')
                helpers.append(dict(self._selection(item), role=role))
            if len(helpers) > capabilities['max_helper_slots']:
                raise ValueError('Select at most 50 helper slots')
            chosen = {}
            for helper in helpers:
                chosen.setdefault(helper['role'], helper)
            selected = list(chosen.values())
            if provider == 'opencode':
                selected = [chosen[role] for role in Runtime.REQUIRED_ROLES if role in chosen]
            identifier = uuid.uuid4().hex
            job = {'id': identifier, 'project_id': target['project_id'], 'path': path,
                   'provider': provider, 'task': task.strip(),
                   'orchestra': {'provider': provider, 'chief': chief, 'helpers': helpers},
                   'status': 'planned', 'created_at': time.time(), 'session_id': None,
                   'plan': {'kind': 'proposed_plan', 'label': 'Proposed plan (local template; no AI call)',
                            'steps': ['Read-only chief scopes the task and assigns bounded ownership.',
                                      'One implementer executes the approved scope.',
                                      'Independent verifier checks the frozen candidate; reviewer reports findings.',
                                      'Stop after two failed attempts or when approval is required.'],
                            'approval_required': True, 'execution_helpers': selected,
                            'configured_helper_count': len(helpers), 'selected_helper_count': len(selected),
                            'job_capabilities': capabilities,
                            'helper_selection': 'The chief can select the first saved helper per role, one at a time. Other saved slots are not invoked; registered roles may remain unused.',
                            'limitations': ['Chief permissions and model effort must be supported by the installed CLI.',
                                            'Worker file ownership is an instruction; sandbox scope is the project.',
                                            'New approvals stop execution and require attention.']},
                   'events': [], 'message': 'Review this proposed plan and select Start to approve execution.'}
            if provider == 'claude':
                job['plan']['limitations'].append('Claude safe mode permits project file tools, but no shell; command tests require attention.')
            if provider == 'opencode':
                job['plan']['helper_selection'] = ('Execution uses the first saved explorer, implementer, verifier and reviewer, one at a time. '
                                                   'Other saved slots and optional roles are not invoked.')
                job['plan']['steps'] = ['Controller runs read-only explorer, then chief returns a bounded JSON plan.',
                                        'Controller dispatches one worker at a time with exact file permissions.',
                                        'Freeze assigned candidate files; independent verifier and reviewer inspect them.',
                                        'Chief reads compact summaries and reports completion or attention.']
                job['plan']['limitations'].extend(['OpenCode sequential mode requires verified native CLI 2.0.3.',
                                                 'Shell execution and job resume are unavailable; command tests require attention.'])
            self.jobs[identifier] = job
            self._save()
            return copy.deepcopy(job)

    @staticmethod
    def _selection(value):
        if not isinstance(value, dict):
            raise ValueError('Model selection is required')
        model, effort = value.get('model'), value.get('effort')
        if not isinstance(model, str) or not model or len(model) > 120:
            raise ValueError('Explicit model is required')
        if effort not in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra', 'minimal', 'none'):
            raise ValueError('Explicit supported effort is required')
        return {'model': model, 'effort': effort}

    def is_active(self, path):
        with self.lock:
            return str(Path(path).resolve()) in self.active

    def start(self, identifier):
        return self._launch(identifier, False)

    def resume(self, identifier):
        return self._launch(identifier, True)

    def _launch(self, identifier, resume):
        with self.lock:
            job = self.get(identifier)
            if self.closed:
                raise ValueError('Job manager is closed')
            if job['path'] in self.active:
                raise ValueError('Another job is active for this project')
            allowed = ('cancelled', 'needs_attention', 'failed') if resume else ('planned',)
            if job['status'] not in allowed:
                raise ValueError('Job is not ready to resume' if resume else 'Review a new plan before starting')
            if resume and not job.get('session_id'):
                raise ValueError('No provider session is available; create a new plan')
            try:
                self.runtime.prepare(job, resume=resume)
            except RuntimeAttention as exc:
                self.jobs[identifier].update(status='needs_attention', message=str(exc))
                self._save()
                return self.get(identifier)
            cancel = threading.Event()
            self._active[job['path']] = identifier
            self.jobs[identifier].update(status='running', owner_pid=os.getpid(), owner_id=self.owner_id, message='Chief is coordinating bounded workers.')
            self._save()
            thread = threading.Thread(target=self._run, args=(identifier, cancel, resume), daemon=True)
            self.threads[identifier] = (thread, cancel)
            thread.start()
            return self.get(identifier)

    def _event(self, identifier, event):
        with self.lock:
            self._reload()
            job = self.jobs[identifier]
            if event.get('session_id'):
                job['session_id'] = event['session_id']
            job['events'] = (job['events'] + [event])[-100:]
            self._save()

    def _run(self, identifier, cancel, resume):
        try:
            result = self.runtime.run(self.get(identifier), cancel, lambda e: self._event(identifier, e), resume=resume)
        except Exception:
            result = {'status': 'needs_attention', 'message': 'Runtime interrupted. Inspect project changes before retrying.'}
        with self.lock:
            self._reload()
            self.jobs[identifier].update(result)
            self._active.pop(self.jobs[identifier]['path'], None)
            self.threads.pop(identifier, None)
            self._save()

    def cancel(self, identifier):
        with self.lock:
            self.get(identifier)
            if identifier in self.threads:
                self.threads[identifier][1].set()
                self.jobs[identifier].update(status='cancelling', message='Stopping process group; existing file changes are retained.')
            elif self.jobs[identifier]['status'] in ('running', 'cancelling'):
                raise ValueError('Job belongs to another live or unverified hub process; cancel it in the owning hub')
            elif self.jobs[identifier]['status'] == 'planned':
                self.jobs[identifier].update(status='cancelled', message='Plan cancelled before execution.')
            self._save()
            return self.get(identifier)

    def close(self):
        with self.lock:
            self.closed = True
            threads = list(self.threads.values())
            for _, cancel in threads:
                cancel.set()
        for thread, _ in threads:
            thread.join(timeout=5)
