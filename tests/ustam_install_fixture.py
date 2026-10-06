"""Disposable real-adapter browser fixture. No job manager or provider execution."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ustam.adapters import AdapterManager
from ustam.core import Hub
from ustam.runtime import RuntimeAttention
from ustam.server import UstamServer

with tempfile.TemporaryDirectory(prefix='ustam-install-ui-') as directory:
    root = Path(directory)
    with patch('ustam.adapters.resolve_cli', side_effect=RuntimeAttention('offline fixture')):
        hub = Hub(root / 'state', adapters=AdapterManager())
        for name in ('Alpha', 'Beta'):
            target = root / name
            target.mkdir()
            hub.projects({'action': 'add', 'path': str(target)})
        teams = {}
        for provider, model in (('codex', 'gpt-6.1-sol'), ('claude', 'sonnet'), ('opencode', 'openai/gpt-6.1-sol')):
            effort = '' if provider == 'opencode' else 'medium'
            team = {'id': provider+'-team', 'name': provider+' team', 'provider': provider,
                    'chief': {'model': model, 'effort': effort}, 'helpers': [
                        {'id': role, 'name': role, 'role': role, 'model': model, 'effort': effort}
                        for role in ('explorer', 'implementer', 'verifier', 'reviewer')],
                    'profile': 'balanced', 'task_type': 'feature', 'concurrency': 1}
            hub.orchestras({'action': 'save', 'orchestra': team})
            teams[provider] = team['id']
        ids = [p['id'] for p in hub.bootstrap()['projects']]
        hub.defaults({'selected_providers': ['codex', 'claude', 'opencode'], 'defaults': {'orchestras': teams}})
        hub.projects({'action': 'selection', 'ids': [ids[0]], 'providers': ['codex', 'claude', 'opencode']})
        server = UstamServer(('127.0.0.1', 0), hub)
        print(json.dumps({'origin': server.origin, 'root': directory, 'ids': ids}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
