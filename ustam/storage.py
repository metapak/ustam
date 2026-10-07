"""Private, atomic hub metadata; never writes inside user projects."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import weakref

_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()

class ProcessRLock:
    """Reentrant local lock plus a stable OS lock shared by hub processes."""
    def __init__(self, path):
        self.path = path
        self.local = threading.RLock()
        self.depth = threading.local()
        self.fd = None
    def acquire(self):
        self.local.acquire()
        depth = getattr(self.depth, "value", 0)
        if depth == 0:
            fd = None
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX)
                self.fd = fd
            except BaseException:
                if fd is not None:
                    os.close(fd)
                self.local.release()
                raise
        self.depth.value = depth + 1
        return True
    def release(self):
        depth = self.depth.value - 1
        self.depth.value = depth
        try:
            if depth == 0:
                fd, self.fd = self.fd, None
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            self.local.release()
    def __enter__(self):
        self.acquire()
        return self
    def __exit__(self, *args):
        self.release()

def process_lock(path):
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = ProcessRLock(path)
        return lock

PROVIDERS = ('codex', 'claude', 'opencode', 'antigravity')

def default_state_dir():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Ustam'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Ustam'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'ustam'

class StateStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or default_state_dir())
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'hub.json'
        self.lock = process_lock(self.directory / '.hub.lock')
        with self.lock:
            self._reload()
    def _reload(self):
        self.data = {'revision': 0, 'defaults': {}, 'selected_providers': ['codex'], 'roots': [], 'projects': [], 'orchestras': [], 'selected_projects': [], 'project_overrides': {}}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(loaded, dict):
                raise ValueError('Invalid hub metadata')
            self.data.update(loaded)
    def read(self):
        with self.lock:
            self._reload()
            return copy.deepcopy(self.data)
    def update(self, change, revision=None):
        with self.lock:
            self._reload()
            if revision is not None and revision != self.data['revision']:
                raise ValueError('Metadata changed; refresh and preview again')
            candidate = copy.deepcopy(self.data)
            change(candidate)
            candidate['revision'] += 1
            fd, name = tempfile.mkstemp(prefix='.hub-', dir=self.directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as output:
                    json.dump(candidate, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            self.data = candidate
            return self.read()
