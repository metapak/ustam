"""Launch the unified hub or its isolated adapter worker."""
import argparse
import sys
import os
import subprocess
import threading
import time
import webbrowser
from urllib.parse import urlsplit


def browser_notice(origin, stopped):
    try:
        address = urlsplit(origin)
        port = address.port
    except ValueError:
        return
    if stopped.is_set():
        return
    if address.scheme != 'http' or address.hostname != '127.0.0.1' or not port:
        return
    message = 'Tarayıcı açılamadı / Browser could not open.\nBu adresi tarayıcınızda açın / Open this address in your browser:\n' + origin
    if sys.platform == 'darwin':
        process = subprocess.Popen(['/usr/bin/osascript', '-e',
            'on run argv\nset choice to display dialog (item 1 of argv) with title "Ustam" buttons {"Kapat / Close", "Tarayıcıda aç / Open browser"} default button 2 giving up after 60\nif gave up of choice is false and button returned of choice is "Tarayıcıda aç / Open browser" then open location (item 2 of argv)\nend run', message, origin])
        try:
            deadline = time.monotonic() + 60
            while process.poll() is None and time.monotonic() < deadline:
                if stopped.wait(.1):
                    break
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
    elif os.name == 'nt':
        import ctypes
        # The dialog belongs to this worker process and closes when it exits.
        ctypes.windll.user32.MessageBoxW(None, message, 'Ustam', 0x30)
    else:
        print(message, file=sys.stderr, flush=True)

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--work-protocol':
        if len(argv) < 2:
            raise SystemExit('Work protocol provider required')
        from .native_protocol import main as work_main
        return work_main(argv[1], argv[2:])
    if '--adapter' in argv:
        # This branch must execute before UI/server setup, including frozen builds.
        index = argv.index('--adapter')
        from . import adapters
        worker = getattr(adapters, 'worker_main', None) or getattr(adapters, 'adapter_main', None)
        if worker is None:
            raise RuntimeError('Adapter worker entry point unavailable')
        return worker(argv[index + 1:])
    parser = argparse.ArgumentParser(description='Ustam local orchestration hub')
    parser.add_argument('--state-dir')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    from .core import Hub
    from .server import UstamServer
    hub = Hub(args.state_dir)
    try:
        from .jobs import JobManager
        hub.jobs = JobManager(hub.store.directory, hub.adapters)
    except ImportError:
        pass
    server = UstamServer(('127.0.0.1', args.port), hub)
    print(server.origin, flush=True)
    notice_stopped = threading.Event()
    notice = None
    if not args.no_browser:
        try:
            opened = webbrowser.open(server.origin)
        except Exception:
            opened = False
        if not opened:
            notice = threading.Thread(target=browser_notice, args=(server.origin, notice_stopped), daemon=True)
            notice.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        notice_stopped.set()
        if notice is not None:
            notice.join(timeout=1.25)
        server.server_close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
