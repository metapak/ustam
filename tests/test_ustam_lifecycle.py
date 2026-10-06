"""Process ownership checks for the Mac launcher; all state is temporary."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('lifecycle_launcher', ROOT / 'launchers/launch_ustam.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def alive(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


@unittest.skipUnless(sys.platform == 'darwin', 'Mac launcher lifecycle')
class MacLifecycleTests(unittest.TestCase):
    def test_cleanup_stops_owned_stubborn_descendant_after_worker_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'child.pid'
            child = f'import os,signal,time; from pathlib import Path; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)'
            code = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(60)'
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
            process = subprocess.Popen([sys.executable, '-c', code], start_new_session=True, stderr=subprocess.DEVNULL)
            childpid = None
            try:
                deadline = time.monotonic() + 5
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                childpid = int(pidfile.read_text())
                process.kill()
                process.wait(timeout=5)
                launcher.stop_worker(process, owned_group=True)
                deadline = time.monotonic() + 3
                while alive(childpid) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse(alive(childpid))
                self.assertIsNone(unrelated.poll())
            finally:
                launcher.stop_worker(process, owned_group=True)
                unrelated.terminate()
                unrelated.wait(timeout=5)

    def test_sigterm_cleanup_reopen_and_safe_port_collision(self):
        executable = os.environ.get('USTAM_NATIVE_LAUNCHER')
        command = [executable] if executable else [sys.executable, str(ROOT / 'launchers/launch_ustam.py')]
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            args = ['--no-browser', '--port', str(port), '--state-dir', str(Path(directory) / 'state')]
            origin = f'http://127.0.0.1:{port}'
            def bootstrap():
                with urllib.request.urlopen(origin + '/api/bootstrap', timeout=1) as response:
                    return json.load(response)
            def wait_ready(process):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        return bootstrap()
                    except OSError:
                        self.assertIsNone(process.poll(), 'Launcher exited before readiness')
                        time.sleep(.1)
                self.fail('Hub did not become ready')
            env = {**os.environ, 'HOME': directory, 'PYTHONHOME': '', 'PYTHONPATH': ''}
            first = subprocess.Popen(command + args, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            second = reopened = None
            workerpid = adapterpid = None
            try:
                data = wait_ready(first)
                def children(pid):
                    rows = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid='], capture_output=True, text=True, check=True).stdout.splitlines()
                    return [int(row.split()[0]) for row in rows if int(row.split()[1]) == pid]
                workerpid = children(first.pid)[0]
                self.assertEqual(os.getpgid(workerpid), workerpid)
                if executable:
                    # An offline preview starts a real adapter child without paid calls.
                    project = Path(directory) / 'project'
                    project.mkdir()
                    def post(route, body):
                        request = urllib.request.Request(origin + route, json.dumps(body).encode(), {'Origin': origin, 'Content-Type': 'application/json', 'X-Ustam-CSRF': data['csrf']})
                        with urllib.request.urlopen(request, timeout=20) as response:
                            return json.load(response)
                    post('/api/projects', {'action': 'add', 'path': str(project)})
                    ident = bootstrap()['projects'][0]['id']
                    preview = post('/api/preview', {'project_ids': [ident], 'provider': 'codex', 'payload': {'name': 'Offline lifecycle check', 'provider': 'codex', 'chief': {'model': 'gpt-6.1-sol', 'effort': 'medium'}, 'helpers': [{'id': 'one', 'role': 'implementer', 'name': 'Implementation', 'model': 'gpt-6.1-sol', 'effort': 'medium'}], 'concurrency': 1, 'profile': 'balanced'}})
                    self.assertTrue(preview['results'][0]['ok'], preview)
                    adapterpid = children(workerpid)[0]
                    self.assertEqual(os.getpgid(adapterpid), workerpid)
                second = subprocess.Popen(command + args, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # On macOS the error alert is bounded but modal; terminate only
                # the duplicate launcher after it has attempted startup.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    rows = subprocess.run(['/bin/ps', '-axo', 'ppid=,command='], capture_output=True, text=True, check=True).stdout.splitlines()
                    if any(row.split()[0] == str(second.pid) and '/usr/bin/osascript' in row and 'Address already in use' in row for row in rows):
                        break
                    time.sleep(.1)
                else:
                    self.fail('Duplicate launcher did not visibly reject the occupied port')
                self.assertIsNone(first.poll())
                self.assertTrue(bootstrap()['ok'])
                second.terminate()
                second.wait(timeout=10)
                first.terminate()
                self.assertEqual(first.wait(timeout=10), 0)
                self.assertFalse(alive(workerpid))
                if adapterpid is not None:
                    self.assertFalse(alive(adapterpid))
                reopened = subprocess.Popen(command + args, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.assertTrue(wait_ready(reopened)['ok'])
                reopened_worker = children(reopened.pid)[0]
                reopened.send_signal(signal.SIGINT)
                self.assertEqual(reopened.wait(timeout=10), 0)
                self.assertFalse(alive(reopened_worker))
            finally:
                for process in (second, first, reopened):
                    if process is not None and process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)

if __name__ == '__main__':
    unittest.main()
