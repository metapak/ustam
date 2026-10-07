"""Read picker-visible Codex models without starting a thread or reading chats."""
from __future__ import annotations
import json
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

CATALOG = Path(__file__).with_name('model_catalog.json')
MODEL_ID = re.compile(r'^gpt-[a-z0-9][a-z0-9._-]{0,89}$', re.I)
EFFORTS = frozenset(('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'))
CURRENT_MODELS = frozenset(('gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna'))
VERSIONED_MODEL = re.compile(r'^gpt-(\d+)(?:\.(\d+))?-[a-z0-9][a-z0-9._-]*$', re.I)


def new_choice_model(model_id: str, *, discovered: bool = False) -> bool:
    """Offer current documented models and newer models observed in the local CLI.

    Older saved selections are handled separately by the console and must not
    become new assignments merely because an old CLI still lists them.
    """
    if model_id in CURRENT_MODELS:
        return True
    match = VERSIONED_MODEL.fullmatch(model_id) if discovered else None
    return bool(match and (int(match[1]), int(match[2] or 0)) > (6, 1))


def bundled():
    """Maintained offline list from the official Codex model page."""
    data = json.loads(CATALOG.read_text(encoding='utf-8'))
    models = []
    for item in data['models']:
        if MODEL_ID.fullmatch(item['id']) and new_choice_model(item['id']):
            models.append({'id': item['id'], 'label': item['label'],
                           'efforts': [effort for effort in item['efforts'] if effort in EFFORTS],
                           'origin': 'documentation'})
    return {'models': models, 'source': data['source'], 'reviewed_at': data['reviewed_at']}


def _reader(pipe, messages):
    try:
        for line in pipe:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                messages.put(value)
    finally:
        messages.put(None)


def discover_cli(timeout=5.0):
    """Use the documented stdio model/list API; return only picker metadata."""
    executable = shutil.which('codex')
    if not executable:
        return None
    try:
        process = subprocess.Popen([executable, 'app-server', '--listen', 'stdio://'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except OSError:
        return None
    messages = queue.Queue()
    reader = threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    def send(message):
        process.stdin.write(json.dumps(message, separators=(',', ':')) + '\n')
        process.stdin.flush()
    def response(identifier):
        while time.monotonic() < deadline:
            try:
                item = messages.get(timeout=max(0.01, deadline-time.monotonic()))
            except queue.Empty:
                break
            if item is None:
                break
            if item.get('id') == identifier:
                return item.get('result') if not item.get('error') else None
        return None
    try:
        send({'method': 'initialize', 'id': 1,
              'params': {'clientInfo': {'name': 'bounded_orchestrator_console',
                                        'title': 'Ustam Console', 'version': '0.6.0'}}})
        if response(1) is None:
            return None
        send({'method': 'initialized', 'params': {}})
        models, cursor = [], None
        for page in range(4):
            params = {'limit': 100, 'includeHidden': False}
            if cursor:
                params['cursor'] = cursor
            request_id = page + 2
            send({'method': 'model/list', 'id': request_id, 'params': params})
            result = response(request_id)
            if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                return None
            for item in result['data']:
                if not isinstance(item, dict):
                    continue
                model_id = item.get('model') or item.get('id')
                if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id) or not new_choice_model(model_id, discovered=True) or item.get('hidden'):
                    continue
                label = item.get('displayName')
                if not isinstance(label, str) or len(label) > 100 or any(ord(c) < 32 for c in label):
                    label = model_id
                efforts = []
                for entry in item.get('supportedReasoningEfforts') or []:
                    effort = entry.get('reasoningEffort') if isinstance(entry, dict) else None
                    if effort in EFFORTS and effort not in efforts:
                        efforts.append(effort)
                models.append({'id': model_id, 'label': label, 'efforts': efforts, 'origin': 'local_cli'})
            cursor = result.get('nextCursor')
            if not cursor:
                break
        else:
            # Refuse a partial catalogue rather than hiding later pages.
            return None
        return models or None
    except (OSError, BrokenPipeError, ValueError):
        return None
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()


def catalog():
    fallback = bundled()
    runtime = discover_cli()
    if runtime:
        by_id = {item['id']: item for item in runtime
                 if new_choice_model(item['id'], discovered=True)}
        for item in fallback['models']:
            by_id.setdefault(item['id'], item)
        return {'models': list(by_id.values()), 'discovery': 'local_cli',
                'checked_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                'documentation_source': fallback['source'], 'documentation_reviewed_at': fallback['reviewed_at'],
                'account_access_verified': False,
                'explanation': 'Local CLI model/list is scoped to this installed CLI and sign-in. Other documented entries may be unavailable. This does not guarantee another Codex client or account can use a model.'}
    return {'models': fallback['models'], 'discovery': 'documentation_fallback',
            'checked_at': None, 'documentation_source': fallback['source'],
            'documentation_reviewed_at': fallback['reviewed_at'], 'account_access_verified': False,
            'explanation': 'Codex CLI model/list unavailable. Documentation fallback is not an account entitlement check.'}
