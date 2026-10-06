"""Private bounded stdio sessions for hash-pinned provider engines."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tempfile
import uuid
from .runtime import RuntimeAttention, cli_environment, resolve_cli

ROOT = Path(__file__).resolve().parents[1]
MAX_MESSAGE = 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
PROVIDERS = ('codex', 'claude', 'opencode')

class AdapterError(ValueError):
    def __init__(self, message, code='adapter_error'):
        super().__init__(message)
        self.code = code

class _Session:
    def __init__(self, provider, timeout, target, usage_cache_dir=None):
        command = [sys.executable, '--adapter', provider] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'ustam', '--adapter', provider]
        env = os.environ.copy()
        try:
            executable = resolve_cli(provider, project=target, env=env)
            env = cli_environment(executable, project=target, env=env)
        except RuntimeAttention:
            # An unavailable/unsafe CLI must never fall back to project PATH.
            # Backend offline catalogs and file operations remain supported.
            env['PATH'] = ''
        if os.name == 'nt':
            env['PATHEXT'] = '.EXE'
            env['NoDefaultCurrentDirectoryInExePath'] = '1'
        env['PYTHONPATH'] = str(ROOT)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env.pop('PYTHONSTARTUP', None)
        env.pop('USTAM_USAGE_INDEX_CACHE', None)
        if provider == 'codex' and usage_cache_dir is not None:
            env['USTAM_USAGE_INDEX_CACHE'] = str(usage_cache_dir)
        self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
        self.timeout, self.responses = timeout, queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_RESPONSE + 1)
                if not line:
                    break
                if len(line) > MAX_RESPONSE or not line.endswith(b'\n'):
                    self.responses.put(AdapterError('Worker response exceeded16MiB bounds',code='bounds'))
                    break
                try:
                    self.responses.put(json.loads(line))
                except (ValueError, UnicodeError):
                    self.responses.put(AdapterError('Invalid worker response'))
                    break
        finally:
            self.responses.put(AdapterError('Adapter worker terminated; inspect project and recovery state before retrying'))

    def request(self, method, target, params, deadline=None):
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        if not self.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise AdapterError('Adapter is busy; retry after the current operation completes')
        try:
            reqid = uuid.uuid4().hex
            data = (json.dumps({'reqid': reqid, 'method': method, 'target': target, 'params': params}, allow_nan=False) + '\n').encode()
            if len(data) > MAX_MESSAGE:
                raise AdapterError('Adapter request exceeded bounds')
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
                response = self.responses.get(timeout=max(0, deadline - time.monotonic()))
            except (OSError, ValueError, queue.Empty) as exc:
                self.close()
                raise AdapterError('Adapter worker unavailable or timed out; inspect project and recovery state before retrying') from exc
            if isinstance(response, Exception):
                self.close()
                raise response
            if not isinstance(response, dict) or response.get('reqid') != reqid:
                self.close()
                raise AdapterError('Worker response identity mismatch')
            if response.get('ok') is not True:
                error = response.get('error', {})
                code = error.get('code') if error.get('code') in ('usage_index_timeout', 'bounds') else 'adapter_error'
                raise AdapterError(error.get('message', 'Adapter request failed'), code=code)
            if not isinstance(response.get('result'), dict):
                raise AdapterError('Invalid adapter result')
            return response['result']
        finally:
            self.lock.release()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

class AdapterManager:
    def __init__(self, timeout=45, usage_cache_dir=None):
        self.timeout = timeout
        self.usage_cache_dir = usage_cache_dir
        self._sessions, self._targets = {}, {}
        self._lock = threading.Lock()
        self._closed = False
        self._scratch = tempfile.TemporaryDirectory(prefix='ustam-catalog-')

    def _call(self, provider, target, method, params=None):
        if provider not in PROVIDERS:
            raise AdapterError('Unknown provider')
        path = Path(target).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise AdapterError('Project directory required')
        if path == ROOT or path.is_relative_to(ROOT / 'ustam/engines'):
            raise AdapterError('Engine distribution cannot be a target')
        key = (provider, str(path))
        with self._lock:
            if self._closed:
                raise AdapterError('Adapter manager is closed')
            session = self._sessions.get(key)
            if session is None or session.process.poll() is not None:
                if session is not None:
                    session.close()
                session = self._sessions[key] = _Session(provider, self.timeout, str(path), self.usage_cache_dir)
            lock = self._targets.setdefault(str(path), threading.Lock())
        deadline = time.monotonic() + self.timeout
        # Every operation sees a consistent project while another provider writes.
        if not lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise AdapterError('Project adapter is busy; retry after the current operation completes')
        try:
            return session.request(method, str(path), params or {}, deadline=deadline)
        finally:
            lock.release()

    def inspect(self, provider, target): return self._call(provider, target, 'inspect')
    def preview(self, provider, target, payload): return self._call(provider, target, 'preview', payload)
    def apply(self, provider, target, preview_id): return self._call(provider, target, 'apply', {'preview_id': preview_id})
    def restore(self, provider, target, payload): return self._call(provider, target, 'restore', payload)
    def usage(self, provider, target): return self._call(provider, target, 'usage')
    def models(self, provider, target=None): return self._call(provider, target or self._scratch.name, 'models')

    def close(self):
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            with session.lock:
                session.close()
        self._scratch.cleanup()
        return {'closed': True}

def worker_main(argv=None):
    from .adapter_worker import main
    return main(argv)
