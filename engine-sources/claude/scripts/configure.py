#!/usr/bin/env python3
"""Open a project-scoped local browser console; run from the installer repository."""
from __future__ import annotations
import argparse
import hmac
import json
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import install

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.claude/tools'))
from console_settings import Settings
from usage_report import report


def make_server(target, port=0):
    settings = Settings(install, ROOT, target)
    token = secrets.token_urlsafe(32)
    pending_uninstall = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body, content_type='application/json'):
            data = body.encode() if isinstance(body, str) else json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def valid(self, authenticated=False):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != origin[7:]:
                return False
            if self.headers.get('Origin') not in (None, origin):
                return False
            if authenticated and not hmac.compare_digest(self.headers.get('X-Console-Token', ''), token):
                return False
            return True

        def do_GET(self):
            if not self.valid(self.path.startswith('/api/')):
                return self.respond(403, {'error': 'Forbidden'})
            try:
                if self.path == '/api/settings':
                    return self.respond(200, settings.read())
                if self.path == '/api/tasks':
                    path = settings.path(Path('.claude/.bounded-orchestrator/tasks.json'))
                    if not path.exists():
                        return self.respond(200, {'status': 'unavailable', 'tasks': [], 'message': 'Bu projede görev defteri yok.'})
                    if path.stat().st_size > 1024 * 1024:
                        raise ValueError('Task ledger exceeds 1 MiB')
                    data = json.loads(path.read_text(encoding='utf-8'))
                    keys = {'id', 'summary', 'role', 'status', 'depends_on', 'created_at', 'updated_at', 'attempts', 'evidence'}
                    return self.respond(200, {'status': 'available', 'tasks': [{k: v for k, v in task.items() if k in keys} for task in data.get('tasks', [])], 'message': 'Yerel görev metadata. Tokenlar ve görev maliyetleri ölçülmüyor.'})
                assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/orchestra.svg': 'orchestra.svg'}
                if self.path not in assets:
                    return self.respond(404, {'error': 'Not found'})
                name = assets[self.path]
                types = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css', 'svg': 'image/svg+xml'}
                self.respond(200, (ROOT / '.claude/tools/console' / name).read_text(encoding='utf-8'), types[name.rsplit('.', 1)[1]])
            except (ValueError, OSError, install.InstallError, TypeError, KeyError):
                self.respond(400, {'error': 'Project data could not be read; inspect local files.'})

        def do_POST(self):
            if not self.valid(True) or self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}':
                return self.respond(403, {'error': 'Forbidden'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 64 * 1024 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('JSON body must be between 1 and 65536 bytes')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('JSON object required')
                stopping = False
                if self.path == '/api/preview':
                    result = settings.plan(payload)[2]
                elif self.path == '/api/save':
                    result = settings.save(payload)
                elif self.path == '/api/uninstall-preview':
                    if payload:
                        raise ValueError('Uninstall preview body must be empty')
                    pending_uninstall.clear()
                    result = settings.uninstall_preview()
                    confirmation = secrets.token_urlsafe(32)
                    pending_uninstall.update(token=confirmation, revision=result['revision'],
                                             target=result['target'], expires=time.monotonic() + 120)
                    result['confirmation'] = confirmation
                elif self.path == '/api/uninstall':
                    if set(payload) != {'confirmation', 'revision', 'target'}:
                        raise ValueError('Fresh uninstall preview required')
                    approved = (isinstance(payload['confirmation'], str)
                                and hmac.compare_digest(payload['confirmation'], pending_uninstall.get('token', ''))
                                and payload['revision'] == pending_uninstall.get('revision')
                                and payload['target'] == pending_uninstall.get('target')
                                and time.monotonic() < pending_uninstall.get('expires', 0))
                    pending_uninstall.clear()
                    if not approved:
                        raise ValueError('Uninstall preview expired or changed; check it again')
                    result = settings.uninstall_confirm(payload['revision'])
                elif self.path == '/api/uninstall-cancel':
                    if set(payload) != {'confirmation'} or not isinstance(payload['confirmation'], str):
                        raise ValueError('Uninstall confirmation required')
                    if hmac.compare_digest(payload['confirmation'], pending_uninstall.get('token', '')):
                        pending_uninstall.clear()
                    result = {'cancelled': True}
                elif self.path == '/api/restore':
                    if payload:
                        raise ValueError('Restore body must be empty')
                    result = settings.restore()
                elif self.path == '/api/quit':
                    if payload:
                        raise ValueError('Quit body must be empty')
                    result = {'stopping': True}
                    stopping = True
                elif self.path == '/api/usage':
                    if set(payload) - {'path', 'start', 'end'}:
                        raise ValueError('Unknown usage fields')
                    for key, value in payload.items():
                        if value is not None and (not isinstance(value, str) or len(value) > 4096):
                            raise ValueError('Invalid usage field')
                    path = Path(payload['path']).expanduser().resolve() if payload.get('path') else None
                    if path and (not path.is_file() or path.suffix.lower() not in {'.json', '.jsonl'}):
                        raise ValueError('Select a local sanitized JSON/JSONL OTLP file')
                    result = report(path, payload.get('start') or None, payload.get('end') or None,
                                    settings.usage_history(), settings.project_hash())
                else:
                    return self.respond(404, {'error': 'Not found'})
                self.respond(200, result)
                if stopping:
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            except install.PartialUninstallError as exc:
                self.respond(409, {'error': str(exc), 'kind': 'partial_uninstall'})
            except (ValueError, OSError, install.InstallError, TypeError, KeyError) as exc:
                # No full settings content, credentials or malformed file bodies are returned.
                self.respond(400, {'error': str(exc) if isinstance(exc, install.InstallError) or type(exc) is ValueError else 'Local operation failed; inspect selected files.'})

    server = HTTPServer(('127.0.0.1', port), Handler)
    server.timeout = 1
    return server, token


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path, help='existing project directory (project scope only)')
    parser.add_argument('--port', type=int, default=0, help='loopback port; default chooses a free port')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    target = args.target.expanduser().resolve()
    if not target.is_dir() or target == ROOT or not 0 <= args.port <= 65535:
        parser.error('Select an existing target project outside this installer; port must be 0..65535')
    server, token = make_server(target, args.port)
    url = f'http://127.0.0.1:{server.server_port}/#token={token}'
    if sys.stdout is not None:
        print('Local console: ' + url, flush=True)
        print('Stop with Ctrl+C or the browser Close console button. Settings writes require explicit Save.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
