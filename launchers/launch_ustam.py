#!/usr/bin/env python3
"""Native launcher for the bundled loopback hub; no project picker."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import json
import re
import signal
import threading
import urllib.request


def worker_path(executable=None):
    executable = Path(executable or sys.executable).resolve()
    if sys.platform == 'darwin':
        return executable.parent.parent / 'Resources' / 'worker' / 'UstamWorker'
    return executable.parent / 'worker' / ('UstamWorker.exe' if os.name == 'nt' else 'UstamWorker')


def console_python(executable=None):
    executable = Path(executable or sys.executable)
    # Source compatibility can be started with pythonw by WScript. Its
    # adapter children require the console interpreter, hidden at creation.
    return str(executable.with_name('python.exe') if executable.name.lower() == 'pythonw.exe' else executable)


def hidden_process_options():
    if os.name != 'nt':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {'startupinfo': startup, 'creationflags': subprocess.CREATE_NO_WINDOW}


def alert(message, stopped=None):
    if sys.platform == 'darwin':
        notice = subprocess.Popen(['/usr/bin/osascript', '-e', 'on run argv\ndisplay alert "Ustam" message (item 1 of argv) as warning giving up after 60\nend run', message])
        try:
            deadline = time.monotonic() + 65
            while notice.poll() is None and time.monotonic() < deadline:
                if stopped is not None and stopped.wait(.1):
                    break
                if stopped is None:
                    time.sleep(.1)
        finally:
            stop_notice(notice)
    elif os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'Ustam', 0x30)
    else:
        print(message, file=sys.stderr)


def stop_notice(notice):
    if notice is not None and notice.poll() is None:
        notice.terminate()
        try:
            notice.wait(timeout=1)
        except subprocess.TimeoutExpired:
            notice.kill()
            notice.wait(timeout=1)


def preparing_notice():
    # A temporary, owned dialog. No security approval or project picker.
    try:
        return subprocess.Popen(['/usr/bin/osascript', '-e',
            'display dialog "Ustam hazırlanıyor / Preparing Ustam\nTarayıcı birazdan açılacak. / Your browser will open shortly." with title "Ustam" buttons {"Tamam / OK"} giving up after 30'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def worker_ready(process, log_path):
    # Only accept a strict URL from this worker, a port owned by this exact PID,
    # and the actual hub response. Never open a URL copied from arbitrary logs.
    with log_path.open('rb') as stream:
        first_lines = stream.read(4096).decode('utf-8', 'replace').splitlines()
    origin = next((line for line in first_lines if re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', line)), None)
    if origin is None:
        return False
    port = int(origin.rsplit(':', 1)[1])
    if not 1 <= port <= 65535:
        return False
    try:
        sockets = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(process.pid),
            '-iTCP', '-sTCP:LISTEN', '-Fn'], capture_output=True, text=True, timeout=1)
        if 'n127.0.0.1:' + str(port) not in sockets.stdout.splitlines():
            return False
        with urllib.request.urlopen(origin + '/api/bootstrap', timeout=1) as response:
            data = json.load(response)
            return response.status == 200 and data.get('ok') is True and isinstance(data.get('capabilities'), dict)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def wait_for_start(process, log_path, notice, timeout=30, stopped=None):
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None and time.monotonic() < deadline:
            if stopped is not None and stopped.is_set():
                return False
            if worker_ready(process, log_path):
                return True
            time.sleep(.1)
        return False
    finally:
        stop_notice(notice)


def stop_worker(process, owned_group=False):
    """Close our worker, then any descendants in its dedicated POSIX group."""
    if process is None:
        return
    if process.poll() is None:
        # The hub's KeyboardInterrupt path closes adapters and job runtimes.
        if owned_group:
            process.send_signal(signal.SIGINT)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    if owned_group:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                break
            except PermissionError:
                if process.poll() is None:
                    raise
                break
            if signum == signal.SIGTERM:
                time.sleep(.2)
    elif process.poll() is None:
        process.kill()
    process.wait(timeout=3)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    frozen = getattr(sys, 'frozen', False)
    command = [str(worker_path()), *argv] if frozen else [console_python(), '-m', 'ustam', *argv]
    if frozen and not Path(command[0]).is_file():
        alert('Ustam runtime is missing. Extract the complete native download and open Ustam again.')
        return 1
    show_start = sys.platform == 'darwin' and '--no-browser' not in argv and '--adapter' not in argv
    # Separate reader/writer handles: seeking the child's stdout handle would
    # change its shared file offset and could overwrite diagnostics.
    with tempfile.TemporaryDirectory(prefix='ustam-launch-') as directory:
        log_path = Path(directory) / 'worker.log'
        notice = preparing_notice() if show_start else None
        with log_path.open('wb') as log:
            process = None
            owned_group = sys.platform == 'darwin'
            stopped = threading.Event()
            previous_handlers = {}
            if owned_group:
                for signum in (signal.SIGTERM, signal.SIGINT):
                    previous_handlers[signum] = signal.signal(signum, lambda *_: stopped.set())
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, cwd=None if frozen else Path(__file__).resolve().parents[1],
                                           start_new_session=owned_group, **hidden_process_options())
                if show_start and not wait_for_start(process, log_path, notice, stopped=stopped):
                    if stopped.is_set():
                        return 0
                    stop_worker(process, owned_group)
                    process = None
                    detail = log_path.read_bytes()[-1800:].decode('utf-8', 'replace')
                    alert('Ustam açılamadı / Ustam could not start.\nUygulamayı tekrar açın. / Try opening the app again.\n' + detail, stopped=stopped)
                    return 1
                while not stopped.is_set():
                    try:
                        result = process.wait(timeout=.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                else:
                    return 0
                if result and not stopped.is_set():
                    stop_worker(process, owned_group)
                    process = None
                    detail = log_path.read_bytes()[-1800:].decode('utf-8', 'replace')
                    alert('Ustam durdu / Ustam stopped.\n' + detail, stopped=stopped)
                return result
            except OSError as exc:
                alert('Ustam açılamadı / Ustam could not start.\n' + str(exc), stopped=stopped)
                return 1
            finally:
                try:
                    stop_worker(process, owned_group)
                    stop_notice(notice)
                finally:
                    for signum, handler in previous_handlers.items():
                        signal.signal(signum, handler)

if __name__ == '__main__':
    raise SystemExit(main())
