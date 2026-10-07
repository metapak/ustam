"""Real disposable-project adapter integration; never executes paid agent jobs."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if sys.version_info < (3, 11):
    raise unittest.SkipTest('Ustam hub adapters require Python 3.11+; legacy backend tests remain supported on Python 3.10.')

from ustam.adapters import AdapterError, AdapterManager
from ustam.runtime import RuntimeAttention
from ustam.adapter_worker import Engine, verify_engine

ROOT = Path(__file__).resolve().parents[1]

def payload(provider):
    model = {'codex': 'gpt-6.1-sol', 'claude': 'claude-sonnet-5-5', 'opencode': 'openai/gpt-6.1-sol'}[provider]
    return {'provider': provider, 'chief': {'model': model, 'effort': '' if provider == 'opencode' else 'medium'},
            'helpers': [{'id': 'one', 'role': 'implementer', 'name': 'Implementation', 'model': model,
                         'effort': '' if provider == 'opencode' else 'medium'}], 'concurrency': 1, 'profile': 'balanced', 'task_type': 'implementation'}

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.target = Path(self.temporary.name) / 'empty project'
        self.target.mkdir()
        self.manager = AdapterManager(timeout=45)
        # Catalog discovery is read-only, but these deterministic fixtures stay offline.
        self.env = patch.dict(os.environ, {'PATH': ''})
        self.env.start()
        from ustam_claude_cli_fixture import create_version_only_cli, resolver
        self.fixture_cli = create_version_only_cli(Path(self.temporary.name))
        self.offline = patch('ustam.adapters.resolve_cli', side_effect=resolver(self.fixture_cli))
        self.offline.start()

    def tearDown(self):
        self.manager.close()
        self.offline.stop()
        self.env.stop()
        self.temporary.cleanup()

    def test_usage_index_cache_is_shared_only_with_codex_workers(self):
        from unittest.mock import Mock
        from ustam.adapters import _Session
        cache = self.target.resolve().parent / 'private-index'
        with patch.dict(os.environ, {'USTAM_USAGE_INDEX_CACHE': '/untrusted/inherited'}), patch('ustam.adapters.subprocess.Popen', return_value=Mock()) as spawn, patch.object(_Session, '_read'):
            for provider in ('codex', 'claude', 'opencode'):
                _Session(provider, 1, str(self.target), cache)
                environment = spawn.call_args.kwargs['env']
                if provider == 'codex':
                    self.assertEqual(environment['USTAM_USAGE_INDEX_CACHE'], str(cache))
                else:
                    self.assertNotIn('USTAM_USAGE_INDEX_CACHE', environment)
            _Session('codex', 1, str(self.target))
            self.assertNotIn('USTAM_USAGE_INDEX_CACHE', spawn.call_args.kwargs['env'])

    def test_bounded_large_worker_response_keeps_all_records_and_inputs_stay1mib(self):
        import io,queue,threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ustam import adapter_worker
        from ustam.adapters import _Session,MAX_MESSAGE,MAX_RESPONSE
        self.assertEqual(MAX_MESSAGE,1024*1024);self.assertEqual(adapter_worker.MAX_MESSAGE,MAX_MESSAGE);self.assertEqual(MAX_RESPONSE,16*1024*1024)
        rows=[{'thread':str(i),'timestamp':'2026-10-06T00:00:00Z','model':'fixture-model','project':'/fixture','role':'unknown','effort':'unknown','session_id':'unknown','usage':{'total_tokens':i,'input_tokens':i}} for i in range(24000)]
        request={'reqid':'a'*32,'method':'usage','target':str(self.target),'params':{}}
        for result,expected in (({'records':rows},True),({'oversize':'x'*MAX_RESPONSE},False)):
            worker=Mock(provider='codex',module=SimpleNamespace(usage=SimpleNamespace(UsageIndexTimeout=ValueError)));worker.call.return_value=result;output=io.BytesIO()
            with patch.object(adapter_worker,'Engine',return_value=worker),patch.object(sys,'stdin',SimpleNamespace(buffer=io.BytesIO((json.dumps(request)+'\n').encode()))),patch.object(sys,'stdout',SimpleNamespace(buffer=output)):
                self.assertEqual(adapter_worker.main(['codex']),0)
            response=json.loads(output.getvalue());self.assertEqual(response['ok'],expected)
            if expected:
                self.assertGreater(len(output.getvalue()),5184501);self.assertEqual(response['result']['records'],rows)
                session=_Session.__new__(_Session);session.responses=queue.Queue();session.process=SimpleNamespace(stdout=io.BytesIO(output.getvalue()));session._read();self.assertEqual(session.responses.get()['result']['records'],rows)
            else:
                self.assertEqual(response['error']['code'],'bounds');self.assertNotIn('result',response)
                session=_Session.__new__(_Session);session.timeout=1;session.lock=threading.Lock();session.responses=queue.Queue();session.process=SimpleNamespace(stdin=io.BytesIO())
                session.responses.put(response)
                with patch('ustam.adapters.uuid.uuid4',return_value=SimpleNamespace(hex='a'*32)):
                    with self.assertRaises(AdapterError) as raised:session.request('usage',str(self.target),{})
                self.assertEqual(raised.exception.code,'bounds')
        session=_Session.__new__(_Session);session.timeout=1;session.lock=threading.Lock();session.responses=queue.Queue();session.process=SimpleNamespace(stdin=io.BytesIO())
        with self.assertRaisesRegex(AdapterError,'request exceeded bounds'):session.request('usage',str(self.target),{'oversize':'x'*MAX_MESSAGE})
        self.assertEqual(session.process.stdin.getvalue(),b'')

    def test_worker_and_session_preserve_only_typed_index_timeout(self):
        import io
        import queue
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ustam import adapter_worker
        from ustam.adapters import _Session
        class UsageIndexTimeout(ValueError):
            pass
        for exception, expected in ((UsageIndexTimeout('index timed out'), 'usage_index_timeout'), (ValueError('other timeout'), 'adapter_error')):
            with self.subTest(expected=expected):
                worker = Mock(provider='codex', module=SimpleNamespace(usage=SimpleNamespace(UsageIndexTimeout=UsageIndexTimeout)))
                worker.call.side_effect = exception
                request = {'reqid':'a'*32,'method':'usage','target':str(self.target),'params':{}}
                output = io.BytesIO()
                with patch.object(adapter_worker, 'Engine', return_value=worker), patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO((json.dumps(request)+'\n').encode()))), patch.object(sys, 'stdout', SimpleNamespace(buffer=output)):
                    self.assertEqual(adapter_worker.main(['codex']), 0)
                response = json.loads(output.getvalue())
                self.assertEqual(response['error']['code'], expected)
                session = _Session.__new__(_Session)
                session.timeout, session.lock, session.responses = 1, threading.Lock(), queue.Queue()
                session.process = SimpleNamespace(stdin=io.BytesIO())
                session.responses.put(response)
                with patch('ustam.adapters.uuid.uuid4', return_value=SimpleNamespace(hex='a'*32)):
                    with self.assertRaises(AdapterError) as raised:
                        session.request('usage', str(self.target), {})
                self.assertEqual(raised.exception.code, expected)

    def test_opencode_usage_collects_project_scoped_timestamped_exports(self):
        from unittest.mock import Mock
        engine = Engine.__new__(Engine)
        engine.provider = 'opencode'
        engine.target = self.target.resolve()
        engine.backend = Mock()
        usage = Mock()
        usage.collect_breakdown.return_value = {'records': [], 'coverage': {'limited_to_recent_sessions': True}}
        with patch.object(engine, 'select'), patch('ustam.adapter_worker.importlib.import_module', return_value=usage):
            result = engine.call('usage', str(self.target), {})
        usage.collect_breakdown.assert_called_once_with(root=self.target.resolve(), project=str(self.target.resolve()))
        usage.collect.assert_not_called()
        self.assertIn('coverage', result)

    def test_usage_passes_canonical_project_filter(self):
        from unittest.mock import Mock
        engine = Engine.__new__(Engine)
        engine.provider = 'codex'
        engine.target = self.target.resolve()
        engine.backend = Mock()
        with patch.object(engine, 'select'):
            engine.call('usage', str(self.target), {})
        engine.backend.report.assert_called_once_with({'project': [str(self.target.resolve())]})

    def test_busy_target_lock_has_bounded_wait(self):
        import threading
        from unittest.mock import Mock
        target = str(self.target.resolve())
        session = Mock()
        session.process.poll.return_value = None
        self.manager._sessions[('codex', target)] = session
        lock = threading.Lock()
        lock.acquire()
        self.manager._targets[target] = lock
        self.manager.timeout = 0.02
        try:
            with self.assertRaisesRegex(AdapterError, 'busy'):
                self.manager.usage('codex', self.target)
            session.request.assert_not_called()
        finally:
            lock.release()
            self.manager._sessions.clear()

    def test_all_provider_sources_are_self_contained_and_match_runtime_closures(self):
        manifest = json.loads((ROOT / 'ustam/engine-manifest.json').read_text())
        self.assertEqual(set(manifest['engines']), {'codex', 'claude', 'opencode', 'antigravity'})
        repositories = set()
        for provider, entry in manifest['engines'].items():
            self.assertEqual(entry['source_prefix'], 'engine-sources/' + provider)
            source = ROOT / entry['source_prefix']
            self.assertTrue(source.is_dir())
            self.assertFalse(source.is_symlink())
            self.assertTrue(source.resolve().is_relative_to(ROOT.resolve()))
            repositories.add(entry['source_repository'])
            for relative, expected in entry['files'].items():
                path = source / relative
                self.assertTrue(path.is_file(), str(path))
                self.assertFalse(path.is_symlink(), str(path))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected, str(path))
        self.assertEqual(repositories, {'https://github.com/metapak/ustam'})

    def test_manifest_closure_verified(self):
        for provider in ('codex', 'claude', 'opencode', 'antigravity'):
            self.assertTrue(verify_engine(provider).is_dir())

    def test_integrity_rejects_tampering_and_extra_assets(self):
        base = self.target.resolve() / 'fake-bundle'
        engine = base / 'engines/codex'
        engine.mkdir(parents=True)
        asset = engine / 'asset.py'
        asset.write_bytes(b'original')
        manifest = {'engines': {'codex': {'files': {'asset.py': hashlib.sha256(b'original').hexdigest()}}}}
        (base / 'engine-manifest.json').write_text(json.dumps(manifest))
        with patch('ustam.adapter_worker.BASE', base):
            self.assertEqual(verify_engine('codex'), engine)
            asset.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                verify_engine('codex')
            asset.write_bytes(b'original')
            (engine / 'extra.py').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'closure'):
                verify_engine('codex')

    def test_target_python_modules_never_imported(self):
        for name in ('install.py', 'console.py', 'dashboard.py', 'console_settings.py'):
            (self.target / name).write_text("raise RuntimeError('target code executed')\n")
        for provider in ('codex', 'claude', 'opencode'):
            self.assertFalse(self.manager.inspect(provider, self.target)['installed'])

    def test_frozen_keeps_only_trusted_bundle_runtime_paths(self):
        bundle = self.target.resolve() / 'bundle'
        snapshot = bundle / 'ustam/engines/codex'
        trusted = [str(bundle / 'base_library.zip'), str(bundle / 'python3.14/lib-dynload'), str(bundle)]
        external = [str(self.target.resolve()), '/untrusted/python', '']
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', str(bundle), create=True), \
             patch.object(sys, 'path', [*trusted, *external]), \
             patch('ustam.adapter_worker.verify_engine', return_value=snapshot), \
             patch('ustam.adapter_worker.os.chdir'), \
             patch('ustam.adapter_worker.importlib.import_module') as load:
            Engine('codex')
            self.assertEqual(sys.path, [str(snapshot / 'scripts'), *trusted])
            self.assertEqual([call.args[0] for call in load.call_args_list], ['install', 'dashboard'])

    def test_adapter_inherits_trusted_cli_environment(self):
        cli = Path(self.temporary.name).resolve() / 'trusted-bin' / ('codex.exe' if os.name == 'nt' else 'codex')
        cli.parent.mkdir()
        cli.write_text('#!/bin/sh\nexit 0\n')
        cli.chmod(0o700)
        self.offline.stop()
        with patch.dict(os.environ, {'PATH': str(self.target) + os.pathsep + '.'}), \
             patch('ustam.adapters.resolve_cli', return_value=str(cli)) as resolve, \
             patch('ustam.adapters.subprocess.Popen', wraps=subprocess.Popen) as launch:
            self.manager.inspect('codex', self.target)
            environment = launch.call_args.kwargs['env']
            bins = environment['PATH'].split(os.pathsep)
            self.assertEqual(bins[0], str(cli.parent))
            self.assertNotIn(str(self.target), bins)
            self.assertNotIn('.', bins)
            self.assertEqual(resolve.call_args.kwargs['project'], str(self.target.resolve()))
        self.offline.start()

    def test_first_install_all_providers(self):
        for provider in ('codex', 'claude', 'opencode'):
            with self.subTest(provider=provider):
                project = self.target / provider
                project.mkdir()
                self.assertFalse(self.manager.inspect(provider, project)['installed'])
                preview = self.manager.preview(provider, project, payload(provider))
                self.assertIn('preview_id', preview)
                self.assertFalse(self.manager.inspect(provider, project)['installed'])
                saved = self.manager.apply(provider, project, preview['preview_id'])
                self.assertIsInstance(saved, dict)
                state = self.manager.inspect(provider, project)
                self.assertTrue(state['installed'])
                self.assertEqual(state['helpers'][0]['role'], 'implementer')
                self.assertEqual(state['chief']['model'], payload(provider)['chief']['model'])

    def test_codex_stale_preview_and_modified_restore(self):
        preview = self.manager.preview('codex', self.target, payload('codex'))
        (self.target / 'AGENTS.md').write_text('external edit\n')
        with self.assertRaisesRegex(AdapterError, 'changed since preview'):
            self.manager.apply('codex', self.target, preview['preview_id'])
        preview = self.manager.preview('codex', self.target, payload('codex'))
        self.manager.apply('codex', self.target, preview['preview_id'])
        config = self.target / '.codex/config.toml'
        config.write_text(config.read_text() + '\n# external edit\n')
        with self.assertRaisesRegex(AdapterError, 'restore refused'):
            self.manager.restore('codex', self.target, {})

    def test_claude_stale_and_restore(self):
        preview = self.manager.preview('claude', self.target, payload('claude'))
        (self.target / '.claude').mkdir()
        (self.target / '.claude/settings.json').write_text('{}')
        with self.assertRaisesRegex(AdapterError, 'stale'):
            self.manager.apply('claude', self.target, preview['preview_id'])
        preview = self.manager.preview('claude', self.target, payload('claude'))
        self.manager.apply('claude', self.target, preview['preview_id'])
        changed = payload('claude')
        changed['chief']['model'] = 'claude-opus-5-5'
        preview = self.manager.preview('claude', self.target, changed)
        self.manager.apply('claude', self.target, preview['preview_id'])
        self.manager.restore('claude', self.target, {})
        self.assertEqual(self.manager.inspect('claude', self.target)['chief']['model'], 'claude-sonnet-5-5')

    def test_claude_real_adapter_restore_preserves_usage_installation_identity(self):
        from ustam.core import Hub
        # Establish a real installed project, then use an older valid installation
        # timestamp as the fixture. Reapply/restore now occur at a different date
        # without depending on sleeps or mocking the backend transaction.
        initial = payload('claude')
        preview = self.manager.preview('claude', self.target, initial)
        self.manager.apply('claude', self.target, preview['preview_id'])
        manifest_path = self.target / '.claude/.bounded-orchestrator/install.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['installed_at'] = '2026-10-01T00:00:00+00:00'
        original = (json.dumps(manifest, indent=3) + '\n').encode()
        manifest_path.write_bytes(original)
        hub = Hub(Path(self.temporary.name) / 'hub-state', adapters=self.manager)
        hub.projects({'action':'add', 'path':str(self.target)})
        ident = hub.bootstrap()['projects'][0]['id']
        hub.usage('claude', ident)
        prior = hub.installations.read()['usage_installations'][ident]['claude']
        changed = payload('claude')
        changed.update(id='claude-regression', name='Claude regression')
        changed['chief']['model'] = 'haiku'
        changed['chief']['effort'] = ''
        preview = hub.batch('preview', {'provider':'claude','project_ids':[ident],'payload':changed})['results'][0]
        self.assertTrue(preview['ok'], preview)
        applied = hub.apply({'preview_ids':[preview['preview_id']]})['results'][0]
        self.assertTrue(applied['ok'], applied)
        self.assertNotEqual(json.loads(manifest_path.read_text())['installed_at'], manifest['installed_at'])
        restored = hub.batch('restore', {'provider':'claude','project_ids':[ident],'payload':{}})['results'][0]
        self.assertTrue(restored['ok'], restored)
        self.assertEqual(self.manager.inspect('claude', self.target)['chief']['model'], 'claude-sonnet-5-5')
        self.assertEqual(manifest_path.read_bytes(), original)
        after = hub.installations.read()['usage_installations'][ident]['claude']
        self.assertEqual(after, prior)
        self.assertEqual(hub.usage('claude', ident)['installation_boundary']['started_at'], prior['started_at'])

    def test_opencode_stale_and_unsupported_restore(self):
        preview = self.manager.preview('opencode', self.target, payload('opencode'))
        (self.target / '.opencode').mkdir()
        (self.target / '.opencode/opencode.jsonc').write_text('{}')
        with self.assertRaisesRegex(AdapterError, 'changed'):
            self.manager.apply('opencode', self.target, preview['preview_id'])
        with self.assertRaisesRegex(AdapterError, 'safe roster restore'):
            self.manager.restore('opencode', self.target, {})
        invalid = payload('opencode')
        invalid['concurrency'] = 2
        with self.assertRaisesRegex(AdapterError, 'Concurrency'):
            self.manager.preview('opencode', self.target, invalid)

    def test_terminated_worker_invalidates_preview(self):
        preview = self.manager.preview('codex', self.target, payload('codex'))
        session = next(iter(self.manager._sessions.values()))
        session.process.kill()
        session.process.wait()
        with self.assertRaisesRegex(AdapterError, 'Fresh preview required'):
            self.manager.apply('codex', self.target, preview['preview_id'])
        self.assertFalse(self.manager.inspect('codex', self.target)['installed'])

    def test_codex_restore_first_install(self):
        preview = self.manager.preview('codex', self.target, payload('codex'))
        self.manager.apply('codex', self.target, preview['preview_id'])
        self.manager.restore('codex', self.target, {})
        self.assertFalse(self.manager.inspect('codex', self.target)['installed'])

    def test_independent_batch_partial_result(self):
        other = self.target / 'other'
        other.mkdir()
        one = self.manager.preview('claude', self.target, payload('claude'))
        two = self.manager.preview('claude', other, payload('claude'))
        (other / '.claude').mkdir()
        (other / '.claude/settings.json').write_text('{}')
        self.manager.apply('claude', self.target, one['preview_id'])
        with self.assertRaises(AdapterError):
            self.manager.apply('claude', other, two['preview_id'])
        self.assertTrue(self.manager.inspect('claude', self.target)['installed'])
        self.assertFalse(self.manager.inspect('claude', other)['installed'])

    def test_models_without_registered_project(self):
        for provider in ('codex', 'claude', 'opencode'):
            catalog = self.manager.models(provider)
            self.assertEqual(catalog['provider'], provider)
            self.assertIn('roles', catalog)
            self.assertIn('efforts', catalog)
            recommendation = catalog['profile_recommendations']['balanced']
            self.assertFalse(recommendation['access_verified'])
            if provider == 'codex':
                self.assertEqual(recommendation['chief'], {'model': 'gpt-6-astra', 'effort': 'medium'})
                self.assertEqual(recommendation['role_models']['implementer'], 'gpt-6.1-sol')
                self.assertEqual(recommendation['concurrency'], 4)
            elif provider == 'claude':
                self.assertEqual(recommendation['chief'], {'model': 'claude-opus-5-5', 'effort': 'xhigh'})
                self.assertIsNone(recommendation['concurrency'])
            else:
                self.assertIsNone(recommendation['chief'])
                self.assertEqual(recommendation['role_steps']['implementer'], 34)

    def test_protocol_rejects_unknown_method(self):
        request = {'reqid': 'a'*32, 'method': 'exec', 'target': str(self.target), 'params': {}}
        result = subprocess.run([sys.executable, '-m', 'ustam', '--adapter', 'claude'], input=json.dumps(request)+'\n', text=True, capture_output=True, cwd=ROOT, timeout=10)
        response = json.loads(result.stdout)
        self.assertFalse(response['ok'])
        self.assertEqual(response['reqid'], request['reqid'])

if __name__ == '__main__':
    unittest.main()
