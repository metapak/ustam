"""Loopback-only stdlib HTTP server with Origin and session CSRF checks."""
import json
import secrets
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .adapters import AdapterError

MAX_BODY = 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024

class UstamServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, hub, ui_dir=None):
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise ValueError('Ustam must bind to a loopback address')
        super().__init__(address, Handler)
        self.hub = hub
        self.csrf = secrets.token_urlsafe(32)
        self.ui_dir = Path(ui_dir or Path(__file__).parent / 'ui').resolve()
        host, port = self.server_address[:2]
        self.origin = f'http://[{host}]:{port}' if ':' in host else f'http://{host}:{port}'
    def server_close(self):
        super().server_close()
        self.hub.close()

class Handler(BaseHTTPRequestHandler):
    server_version = 'Ustam'
    def log_message(self, *args):
        pass
    def reply(self, status, value):
        content = json.dumps(value, ensure_ascii=False).encode()
        if len(content) > MAX_RESPONSE:
            status=413
            content=json.dumps({'ok':False,'error':{'code':'bounds','message':'API response exceeded16MiB bounds'}}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)
    def error_reply(self, status, code, message):
        self.reply(status, {'ok': False, 'error': {'code': code, 'message': message}})
    def trusted_host(self):
        return self.headers.get('Host') == urlsplit(self.server.origin).netloc
    def do_GET(self):
        if not self.trusted_host():
            return self.error_reply(403, 'host', 'Untrusted host')
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            return self.error_reply(403, 'origin', 'Untrusted origin')
        request = urlsplit(self.path)
        hub = self.server.hub
        try:
            query = parse_qs(request.query)
            if request.path == '/api/bootstrap':
                result = hub.bootstrap()
                result['csrf'] = self.server.csrf
            elif request.path == '/api/works':
                result = hub.work_list()
            elif request.path == '/api/jobs':
                result = {'jobs': hub.jobs.list() if hub.jobs else []}
            elif request.path.startswith('/api/jobs/'):
                if not hub.jobs:
                    raise ValueError('Job manager unavailable')
                result = {'job': hub.jobs.get(request.path.rsplit('/', 1)[1])}
            elif request.path in ('/api/usage', '/api/models'):
                provider = hub.provider(query.get('provider', ['codex'])[0])
                ident = query.get('project_id', [None])[0]
                target = hub.targets([ident])[0]['path'] if ident else None
                if request.path == '/api/usage' and target is None:
                    raise ValueError('Select a project for usage')
                result = {'result': hub.usage(provider, ident) if request.path == '/api/usage' else hub.adapters.models(provider, target)}
            elif request.path.startswith('/api/'):
                return self.error_reply(404, 'not_found', 'Unknown API route')
            else:
                return self.static(request.path)
            self.reply(200, {'ok': True, **result})
        except AdapterError as error:
            indexing = error.code == 'usage_index_timeout'
            bounded=error.code=='bounds'
            self.error_reply(408 if indexing else 413 if bounded else 400, error.code if indexing or bounded else 'invalid_request', str(error))
        except (ValueError, KeyError, TypeError) as error:
            self.error_reply(400, 'invalid_request', str(error))
        except Exception:
            self.error_reply(500, 'internal', 'Operation failed; check local provider configuration')
    def do_POST(self):
        if not self.trusted_host() or self.headers.get('Origin') != self.server.origin:
            return self.error_reply(403, 'origin', 'Untrusted host or origin')
        if not secrets.compare_digest(self.headers.get('X-Ustam-CSRF', ''), self.server.csrf):
            return self.error_reply(403, 'csrf', 'Refresh Ustam before submitting')
        if self.headers.get('Transfer-Encoding'):
            return self.error_reply(400, 'invalid_request', 'Chunked requests are unsupported')
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            size = -1
        if not 0 < size <= MAX_BODY:
            return self.error_reply(413, 'body_size', 'Request body must be between 1 byte and 1 MiB')
        if self.headers.get_content_type() != 'application/json':
            return self.error_reply(415, 'content_type', 'JSON required')
        try:
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('JSON object required')
            hub = self.server.hub
            path = urlsplit(self.path).path
            handlers = {'/api/works': hub.work_action, '/api/projects/pick': hub.pick_project_directory, '/api/projects': hub.projects, '/api/orchestras': hub.orchestras, '/api/defaults': hub.defaults, '/api/preview': lambda b: hub.batch('preview', b), '/api/apply': hub.apply, '/api/restore': lambda b: hub.batch('restore', b), '/api/usage': lambda b: hub.batch('usage', b), '/api/jobs': hub.job_action}
            if path not in handlers:
                return self.error_reply(404, 'not_found', 'Unknown API route')
            result = handlers[path](body)
            self.reply(200, {'ok': True, **result})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.error_reply(400, 'invalid_request', str(error))
        except Exception:
            self.error_reply(500, 'internal', 'Operation failed; check local provider configuration')
    def static(self, path):
        import mimetypes
        from urllib.parse import unquote
        filename = unquote(path).lstrip('/') or 'index.html'
        target = (self.server.ui_dir / filename).resolve()
        if not target.is_relative_to(self.server.ui_dir) or not target.is_file():
            return self.error_reply(404, 'not_found', 'Page not found')
        content = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)
