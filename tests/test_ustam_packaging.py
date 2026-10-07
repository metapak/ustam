"""Focused package integrity, native launcher, and frozen worker smoke checks."""
import importlib.util
import json
import queue
import signal
import socket
import time
import threading
import urllib.request
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if sys.version_info < (3, 11):
    raise unittest.SkipTest('Ustam native build and source hub require Python 3.11+')
import uuid

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

builder = load('ustam_builder', 'scripts/build_ustam_app.py')
launcher = load('ustam_launcher', 'launchers/launch_ustam.py')
syncer = load('ustam_syncer', 'scripts/sync_ustam_distribution.py')
engine_builder = load('ustam_engine_builder', 'scripts/build_ustam_engines.py')


class PackagingTests(unittest.TestCase):
    def test_immutable_engine_source_prefix_excludes_private_and_generated_files(self):
        prefix = 'engine-sources/antigravity/'
        names = [prefix+'VERSION', prefix+'.antigravity/tools/work_protocol.py',
                 prefix+'scripts/install.py', prefix+'tests/private_fixture.json',
                 prefix+'qa-artifacts/private.json', 'ustam/engines/antigravity/VERSION',
                 'engine-sources/antigravity-other/scripts/install.py']
        tree = b''.join(b'100644 blob '+b'a'*40+b'\t'+name.encode()+b'\0' for name in names)
        def read(repo, *args):
            if args[0] == 'ls-tree':
                return tree
            self.assertEqual(args[0], 'show')
            return args[1].encode()
        with patch.object(engine_builder, 'git', side_effect=read) as reader:
            files = engine_builder.frozen_files(ROOT, 'a'*40, prefix)
        self.assertEqual(set(files), {'VERSION', '.antigravity/tools/work_protocol.py', 'scripts/install.py'})
        self.assertTrue(all(data.startswith(('a'*40+':'+prefix).encode()) for data in files.values()))
        self.assertEqual(reader.call_count, 4)

    def test_immutable_engine_source_rejects_unsafe_prefix_pin_and_symlink(self):
        for prefix in ('/', '/absolute', '../outside', 'engine-sources/../outside', 'engine-sources//outside'):
            with self.assertRaisesRegex(ValueError, 'prefix'):
                engine_builder.frozen_files(ROOT, 'a'*40, prefix)
        with self.assertRaisesRegex(ValueError, 'commit ID'):
            engine_builder.frozen_files(ROOT, 'HEAD')
        with patch.object(engine_builder, 'git', return_value=b'120000 blob '+b'a'*40+b'\tengine-sources/antigravity/scripts/install.py\0'):
            with self.assertRaisesRegex(ValueError, 'Nonregular'):
                engine_builder.frozen_files(ROOT, 'a'*40, 'engine-sources/antigravity')
        with patch.object(engine_builder, 'git', return_value=b''):
            with self.assertRaisesRegex(ValueError, 'Empty'):
                engine_builder.frozen_files(ROOT, 'a'*40, 'engine-sources/antigravity')

    def test_single_repository_pins_match_origin_inventory(self):
        import hashlib
        provenance = json.loads((ROOT/'engine-sources/provenance.json').read_text())
        self.assertEqual(set(engine_builder.PINS), set(provenance['engines']))
        commits = {record[1] for record in engine_builder.PINS.values()}
        self.assertEqual(len(commits), 1)
        for provider, (repository, commit, prefix) in engine_builder.PINS.items():
            self.assertEqual(repository, 'https://github.com/metapak/ustam')
            self.assertEqual(prefix, 'engine-sources/' + provider)
            frozen = engine_builder.frozen_files(ROOT, commit, prefix)
            record = provenance['engines'][provider]
            self.assertEqual(set(frozen), set(record['files']))
            for name, data in frozen.items():
                self.assertEqual(hashlib.sha256(data).hexdigest(), record['files'][name]['sha256'])
                self.assertIn(prefix+'/'+name, syncer.FILES)
                self.assertEqual(data, (ROOT/'ustam/engines'/provider/name).read_bytes())

    def test_engine_regeneration_uses_primary_checkout_only(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory)/'arbitrary-checkout-name'
            primary.mkdir()
            with patch.object(engine_builder, 'ROOT', primary), patch.object(engine_builder, 'frozen_files', return_value={'VERSION': b'fixture'}) as reader:
                engine_builder.build()
            self.assertEqual(reader.call_count, 4)
            self.assertTrue(all(call.args[0] == primary for call in reader.call_args_list))
            manifest = json.loads((primary/'ustam/engine-manifest.json').read_text())
            for provider, record in manifest['engines'].items():
                self.assertEqual(record['source_repository'], 'https://github.com/metapak/ustam')
                self.assertEqual(record['source_prefix'], 'engine-sources/'+provider)

    def test_startup_notice_is_bounded_and_closed_on_ready_or_timeout(self):
        from unittest.mock import Mock
        process = Mock()
        process.poll.return_value = None
        for ready in (True, False):
            notice = Mock()
            with patch.object(launcher, 'worker_ready', return_value=ready), patch.object(launcher, 'stop_notice') as stop, patch.object(launcher.time, 'monotonic', side_effect=[0, 0, 31]), patch.object(launcher.time, 'sleep'):
                self.assertEqual(launcher.wait_for_start(process, Path('/unused'), notice), ready)
                stop.assert_called_once_with(notice)

    def test_native_startup_early_exit_is_visible(self):
        from unittest.mock import Mock
        process = Mock()
        process.poll.return_value = 0
        process.wait.return_value = 0
        with patch.object(launcher.sys, 'platform', 'darwin'), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher, 'worker_path', return_value=Path(__file__)), patch.object(launcher, 'preparing_notice', return_value=None), patch.object(launcher.subprocess, 'Popen', return_value=process), patch.object(launcher, 'alert') as alert, patch.object(launcher, 'stop_worker'):
            self.assertEqual(launcher.main([]), 1)
            self.assertIn('could not start', alert.call_args.args[0])

    def test_readiness_rejects_foreign_urls_and_requires_worker_owned_port(self):
        from unittest.mock import Mock
        process = Mock(pid=12345)
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'log'
            log.write_text('https://example.com\nhttp://127.0.0.1:99999\n')
            with patch.object(launcher.subprocess, 'run') as sockets, patch.object(launcher.urllib.request, 'urlopen') as http:
                self.assertFalse(launcher.worker_ready(process, log))
                sockets.assert_not_called()
                http.assert_not_called()
            log.write_text('http://127.0.0.1:43210\n')
            with patch.object(launcher.subprocess, 'run', return_value=Mock(stdout='n127.0.0.1:54321\n')), patch.object(launcher.urllib.request, 'urlopen') as http:
                self.assertFalse(launcher.worker_ready(process, log))
                http.assert_not_called()

    def test_installer_dead_helper_cleanup_does_not_signal_reused_pid(self):
        installer = load('ustam_install_pid_reuse', 'scripts/install_ustam_macos.py')
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / 'stage-process.json').write_text(json.dumps({'pid':12345,'identity':'old creation time and command'}))
            with patch.object(installer, 'process_identity', return_value='new unrelated process'), patch.object(installer.os, 'killpg', create=True) as kill:
                installer.stop_recorded_stage(job)
                kill.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Mac/POSIX installer group cleanup')
    def test_installer_dead_helper_reports_error_and_stops_build(self):
        installer = load('ustam_install_dead_helper', 'scripts/install_ustam_macos.py')
        with tempfile.TemporaryDirectory(prefix="ustam helper's ") as directory:
            job = Path(directory)
            old_app = job / 'Applications/Ustam.app'
            old_app.mkdir(parents=True)
            (old_app / 'keep').write_bytes(b'original')
            child_pid = job / 'child.pid'
            child_code = ('import os,signal,time; from pathlib import Path; '
                          'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                          'Path(' + repr(str(child_pid)) + ').write_text(str(os.getpid())); time.sleep(60)')
            stage_code = 'import subprocess,time; subprocess.Popen([' + repr(sys.executable) + ',"-c",' + repr(child_code) + ']); time.sleep(.5)'
            script = ('import importlib.util; from pathlib import Path; '
                      's=importlib.util.spec_from_file_location("i",' + repr(str(ROOT / 'scripts/install_ustam_macos.py')) + '); '
                      'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                      'm.write_status(Path(' + repr(str(job)) + '),"building"); '
                      'm.run_stage([' + repr(sys.executable) + ',"-c",' + repr(stage_code) + '],Path(' + repr(str(job)) + '),Path(' + repr(str(ROOT)) + '))')
            helper = subprocess.Popen([sys.executable, '-c', script], start_new_session=True)
            installer.save_identity(job, 'helper-process', helper.pid)
            try:
                for _ in range(100):
                    if (job / 'stage-process.json').exists() and child_pid.exists():
                        break
                    time.sleep(.02)
                else:
                    self.fail('Fixture build did not become ready')
                stage = json.loads((job / 'stage-process.json').read_text())
                helper.kill()
                helper.wait(timeout=3)
                time.sleep(.7)  # Build command exits before the next UI status poll.
                started = time.monotonic()
                status = installer.job_status(job)
                self.assertEqual(status['phase'], 'error')
                self.assertIn('build.log', status['detail'])
                self.assertLess(time.monotonic()-started, 5)
                for _ in range(50):
                    if installer.process_identity(stage['pid']) is None:
                        break
                    time.sleep(.02)
                self.assertIsNone(installer.process_identity(stage['pid']))
                self.assertIsNone(installer.process_identity(int(child_pid.read_text())))
                self.assertEqual((old_app / 'keep').read_bytes(), b'original')
            finally:
                if helper.poll() is None:
                    helper.kill()
                    helper.wait(timeout=3)
                installer.stop_recorded_stage(job)

    def test_local_installer_preserves_state_and_managed_backup(self):
        from unittest.mock import Mock
        import plistlib
        installer = load('ustam_install_backup', 'scripts/install_ustam_macos.py')
        with tempfile.TemporaryDirectory(prefix="ustam path's ") as directory:
            root = Path(directory)
            bundle = root / 'built/Ustam.app'
            (bundle / 'Contents').mkdir(parents=True)
            (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': installer.IDENTIFIER}))
            (bundle / 'new').write_text('new')
            home = root / 'home'
            old = home / 'Applications/Ustam.app'
            (old / 'Contents').mkdir(parents=True)
            (old / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': installer.IDENTIFIER}))
            (old / 'old').write_text('old')
            state = home / 'Library/Application Support/Ustam/hub.json'
            state.parent.mkdir(parents=True)
            state.write_bytes(b'private preserved bytes')
            check = Mock()
            installed = installer.install_bundle(bundle, home, check)
            self.assertEqual((installed / 'new').read_text(), 'new')
            backups = list(old.parent.glob('Ustam-backup-*.app'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'old').read_text(), 'old')
            self.assertEqual(state.read_bytes(), b'private preserved bytes')
            self.assertEqual(check.verify_mac_native_code.call_count, 2)

    def test_local_installer_rejects_unmanaged_and_cancelled_replace(self):
        from unittest.mock import Mock
        installer = load('ustam_install_reject', 'scripts/install_ustam_macos.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'built.app'
            bundle.mkdir()
            home = root / 'home'
            target = home / 'Applications/Ustam.app'
            target.mkdir(parents=True)
            (target / 'sentinel').write_text('keep')
            with self.assertRaisesRegex(ValueError, 'unmanaged'):
                installer.install_bundle(bundle, home, Mock())
            self.assertEqual((target / 'sentinel').read_text(), 'keep')
            with patch.object(installer, 'managed_app', return_value=True):
                with self.assertRaises(InterruptedError):
                    installer.install_bundle(bundle, home, Mock(), cancelled=lambda: True)
            self.assertEqual((target / 'sentinel').read_text(), 'keep')
            self.assertFalse(list(target.parent.glob('Ustam-backup-*')))

    def test_local_installer_preflight_and_missing_python(self):
        installer = load('ustam_install_preflight', 'scripts/install_ustam_macos.py')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                installer.source_builder(directory)
        with patch.object(Path, 'is_file', return_value=False), patch.object(Path, 'glob', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'python.org'):
                installer.trusted_python()
        with tempfile.TemporaryDirectory() as directory:
            import shutil
            source = Path(directory)
            (source / 'scripts').mkdir()
            (source / 'launchers').mkdir()
            (source / 'ustam').mkdir()
            shutil.copyfile(ROOT / 'scripts/build_ustam_app.py', source / 'scripts/build_ustam_app.py')
            (source / 'launchers/ustam_worker.py').write_text('')
            (source / 'ustam/engine-manifest.json').write_text('{"schema":0}')
            with self.assertRaisesRegex(ValueError, 'Invalid engine manifest'):
                installer.source_builder(source)

    def test_installer_rejects_source_before_executing_builder(self):
        installer = load('ustam_install_untrusted', 'scripts/install_ustam_macos.py')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'scripts').mkdir()
            (source / 'launchers').mkdir()
            (source / 'ustam').mkdir()
            marker = source / 'executed'
            malicious = source / 'scripts/build_ustam_app.py'
            malicious.write_text('from pathlib import Path; Path(' + repr(str(marker)) + ').touch()')
            (source / 'launchers/ustam_worker.py').write_text('')
            (source / 'ustam/engine-manifest.json').write_text('{"schema":0}')
            with self.assertRaisesRegex(ValueError, 'Invalid engine manifest'):
                installer.source_builder(source)
            self.assertFalse(marker.exists())
            malicious.rename(source / 'outside-builder.py')
            malicious.symlink_to(source / 'outside-builder.py')
            with self.assertRaisesRegex(ValueError, 'Source symlink refused'):
                installer.source_builder(source)
            self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == 'nt', 'Installer process groups are macOS/POSIX only')
    def test_local_installer_failure_and_cancel_stop_owned_process(self):
        installer = load('ustam_install_cancel', 'scripts/install_ustam_macos.py')
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                installer.run_stage([sys.executable, '-c', 'raise SystemExit(7)'], job, ROOT)
            started = job / 'pid'
            script = 'import os,time; from pathlib import Path; Path(' + repr(str(started)) + ').write_text(str(os.getpid())); time.sleep(30)'
            def cancel():
                for _ in range(100):
                    if started.exists():
                        (job / 'cancel').touch()
                        return
                    time.sleep(.02)
            thread = threading.Thread(target=cancel)
            thread.start()
            with self.assertRaises(InterruptedError):
                installer.run_stage([sys.executable, '-c', script], job, ROOT)
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            with self.assertRaises(ProcessLookupError):
                os.kill(int(started.read_text()), 0)

    def test_browser_failure_notice_preserves_server_start_and_shutdown(self):
        from ustam import __main__ as entry
        for failure in (False, RuntimeError('browser unavailable')):
            with self.subTest(failure=str(failure)):
                started = threading.Event()
                received = []

                def notice(origin, stopped):
                    received.append(origin)
                    started.set()
                    stopped.wait(2)

                def serve():
                    self.assertTrue(started.wait(1))
                    raise KeyboardInterrupt

                from unittest.mock import Mock
                server = Mock(origin='http://127.0.0.1:43210')
                server.serve_forever.side_effect = serve
                with patch('ustam.core.Hub'), patch('ustam.jobs.JobManager'), patch('ustam.server.UstamServer', return_value=server), patch.object(entry, 'browser_notice', side_effect=notice), patch.object(entry.webbrowser, 'open', side_effect=failure if isinstance(failure, Exception) else None, return_value=failure):
                    self.assertEqual(entry.main(['--state-dir', '/unused-mock-state']), 0)
                self.assertEqual(received, [server.origin])
                server.serve_forever.assert_called_once()
                server.server_close.assert_called_once()

    def test_mac_browser_notice_stops_its_dialog_process(self):
        from ustam import __main__ as entry
        from unittest.mock import Mock
        process = Mock()
        process.poll.return_value = None
        stopped = Mock()
        stopped.wait.return_value = True
        stopped.is_set.return_value = False
        with patch.object(entry.sys, 'platform', 'darwin'), patch.object(entry.subprocess, 'Popen', return_value=process) as popen:
            entry.browser_notice('http://127.0.0.1:43210', stopped)
        self.assertIn('http://127.0.0.1:43210', popen.call_args.args[0][-2])
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=1)
        process.kill.assert_not_called()

    @unittest.skipUnless(os.environ.get('USTAM_NATIVE_LAUNCHER') and sys.platform == 'darwin', 'Mac nested framework resource seal regression')
    def test_mac_worker_framework_requires_independent_resource_seal(self):
        import shutil
        app = next(p for p in Path(os.environ['USTAM_NATIVE_LAUNCHER']).resolve().parents if p.suffix == '.app')
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / 'Ustam.app'
            shutil.copytree(app, copied, symlinks=True)
            worker = copied / 'Contents/Resources/worker'
            frameworks = [p for p in worker.rglob('*.framework') if p.is_dir() and not p.is_symlink()]
            self.assertTrue(frameworks, 'Fixture must include the bundled worker Python framework')
            seals = list(frameworks[0].rglob('_CodeSignature'))
            self.assertTrue(seals)
            for seal in seals:
                shutil.rmtree(seal)
            # The old outer-only verification passes despite this nested defect.
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(copied)], check=True, capture_output=True)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(copied)], check=True, capture_output=True)
            with self.assertRaises(subprocess.CalledProcessError):
                builder.verify_mac_native_code(copied)
            counts = builder.seal_mac_bundle(copied, worker)
            self.assertGreater(counts['binaries'], 0)

    @unittest.skipUnless(os.environ.get('USTAM_NATIVE_LAUNCHER') and sys.platform == 'darwin', 'Mac archive signature roundtrip')
    def test_mac_archive_preserves_code_signature(self):
        app = next(p for p in Path(os.environ['USTAM_NATIVE_LAUNCHER']).resolve().parents if p.suffix == '.app')
        package = app.parent
        archive = Path(str(package) + '.zip')
        self.assertTrue(archive.is_file())
        builder.verify_mac_archive(archive, package.name)

    def test_manifest_closure_hash_and_ui_assets(self):
        manifest = builder.verify_assets()
        self.assertEqual(set(manifest['engines']), {'codex', 'claude', 'opencode', 'antigravity'})
        for record in manifest['engines'].values():
            self.assertRegex(record['commit'], r'^[a-f0-9]{40}$')
            self.assertTrue(record['files'])

    def test_manifest_rejects_added_or_tampered_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ustam').mkdir()
            manifest = {'schema': 1, 'engines': {}}
            import hashlib
            for provider in ('codex', 'claude', 'opencode', 'antigravity'):
                base = root / 'ustam/engines' / provider
                base.mkdir(parents=True)
                (base / 'VERSION').write_text('1')
                manifest['engines'][provider] = {'commit': 'a'*40, 'files': {'VERSION': hashlib.sha256(b'1').hexdigest()}}
            (root / 'ustam/engine-manifest.json').write_text(json.dumps(manifest))
            (root / 'ustam/ui').mkdir()
            for name in ('index.html','app.js','style.css','icons.mjs'):
                (root / 'ustam/ui' / name).write_text('')
            builder.verify_assets(root)
            (root / 'ustam/ui/icons.mjs').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing hub UI asset: icons.mjs'):
                builder.verify_assets(root)
            (root / 'ustam/ui/icons.mjs').write_text('')
            (root / 'ustam/engines/codex/extra').write_text('')
            with self.assertRaisesRegex(ValueError, 'closure'):
                builder.verify_assets(root)
            (root / 'ustam/engines/codex/extra').unlink()
            (root / 'ustam/engines/codex/VERSION').write_text('2')
            with self.assertRaisesRegex(ValueError, 'hash'):
                builder.verify_assets(root)

    def test_generated_sync_preserves_unowned_and_locally_edited_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'source', Path(directory)/'target'
            source.mkdir(); target.mkdir(); (target/'.git').mkdir()
            (source/'ustam').mkdir(); (source/'ustam/shared.py').write_text('first')
            legacy = target/'legacy.py'; legacy.write_text('provider-specific')
            with patch.object(syncer, 'FILES', ()):
                syncer.sync(target, True, source)
                self.assertEqual(legacy.read_text(), 'provider-specific')
                (source/'ustam/shared.py').write_text('next')
                (target/'ustam/shared.py').write_text('local edit')
                with self.assertRaisesRegex(ValueError, 'locally edited'):
                    syncer.sync(target, True, source)
                self.assertEqual((target/'ustam/shared.py').read_text(), 'local edit')
                (target/'ustam/shared.py').write_text('first')
                syncer.sync(target, True, source)
                self.assertEqual((target/'ustam/shared.py').read_text(), 'next')
                self.assertEqual(syncer.sync(target, False, source), [])
                manifest_path = target/syncer.MANIFEST
                manifest = json.loads(manifest_path.read_text())
                manifest['files']['legacy.py'] = syncer.digest(legacy)
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'unowned path'):
                    syncer.sync(target, True, source)
                self.assertEqual(legacy.read_text(), 'provider-specific')

    @unittest.skipUnless(os.environ.get('USTAM_NATIVE_LAUNCHER') and os.sys.platform == 'darwin', 'Mac native app self-contained check')
    def test_mac_app_runs_on_its_own_outside_package(self):
        import shutil
        executable = Path(os.environ['USTAM_NATIVE_LAUNCHER']).resolve()
        app = next(parent for parent in executable.parents if parent.name.endswith('.app'))
        import plistlib
        bundle_metadata = plistlib.loads((app/'Contents/Info.plist').read_bytes())
        self.assertEqual(bundle_metadata['UstamVersion'], (ROOT/'ustam/VERSION').read_text().strip())
        with tempfile.TemporaryDirectory() as directory:
            moved = Path(directory)/'standalone'/app.name
            shutil.copytree(app, moved, symlinks=True)
            with patch.dict(os.environ, {'USTAM_NATIVE_LAUNCHER':str(moved/'Contents/MacOS/Ustam'),
                                        'USTAM_FROZEN_WORKER':str(moved/'Contents/Resources/worker/UstamWorker')}):
                self.test_native_app_launches_hub_without_picker()
                self.test_frozen_clean_first_launch_and_provider_install()
                self.test_frozen_adapter_stdio_all_providers()

    def test_source_pythonw_uses_console_worker_interpreter(self):
        self.assertEqual(launcher.console_python('/runtime/pythonw.exe'), str(Path('/runtime/python.exe')))
        self.assertEqual(launcher.console_python('/runtime/python3'), str(Path('/runtime/python3')))

    def test_mac_app_worker_is_inside_bundle(self):
        with patch.object(launcher.sys, 'platform', 'darwin'):
            executable = Path.cwd() / 'Applications/Ustam.app/Contents/MacOS/Ustam'
            self.assertEqual(launcher.worker_path(executable), executable.parent.parent / 'Resources/worker/UstamWorker')

    def test_native_launcher_no_project_picker_or_python_dependency(self):
        with patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher, 'worker_path', return_value=Path(__file__)), patch.object(launcher.subprocess, 'Popen') as popen:
            popen.return_value.wait.return_value = 0
            self.assertEqual(launcher.main(['--no-browser']), 0)
            self.assertEqual(popen.call_args.args[0], [str(Path(__file__)), '--no-browser'])
            self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)

    @unittest.skipUnless(os.environ.get('USTAM_FROZEN_WORKER'), 'Set USTAM_FROZEN_WORKER after building a native package')
    def test_frozen_adapter_stdio_all_providers(self):
        worker = os.environ['USTAM_FROZEN_WORKER']
        with tempfile.TemporaryDirectory() as directory:
            for provider in ('codex', 'claude', 'opencode', 'antigravity'):
                project = Path(directory) / provider
                project.mkdir()
                request = {'reqid': uuid.uuid4().hex, 'method': 'inspect', 'target': str(project), 'params': {}}
                result = subprocess.run([worker, '--adapter', provider], input=json.dumps(request)+'\n',
                                        capture_output=True, text=True, timeout=30,
                                        env={**os.environ, 'PATH': '', 'HOME': directory, 'USERPROFILE': directory, 'LOCALAPPDATA': directory, 'APPDATA': directory, 'PYTHONPATH': '', 'PYTHONHOME': '', 'PYTHONDONTWRITEBYTECODE': '1'})
                self.assertEqual(result.returncode, 0, result.stderr)
                response = json.loads(result.stdout)
                self.assertEqual(response['reqid'], request['reqid'])
                self.assertTrue(response['ok'], response)

    @unittest.skipUnless(os.environ.get('USTAM_FROZEN_WORKER'), 'Set USTAM_FROZEN_WORKER after building a native package')
    def test_frozen_clean_first_launch_and_provider_install(self):
        worker = os.environ['USTAM_FROZEN_WORKER']
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            bins = Path(directory) / 'fixture-bin'
            bins.mkdir()
            agy = bins / ('agy.cmd' if os.name == 'nt' else 'agy')
            if os.name == 'nt':
                agy.write_text('@echo off\nif "%~1"=="--version" (echo 1.2.16) else if "%~1"=="--help" (echo --agent --model --output-format agents) else (exit /b 9)\n')
            else:
                agy.write_text('#!/bin/sh\ncase "$1" in\n--version) printf "1.2.16\\n";;\n--help) printf "%s\\n" "--agent --model --output-format agents";;\n*) exit 9;;\nesac\n')
                agy.chmod(0o755)
            from ustam_claude_cli_fixture import create_version_only_cli
            import shutil
            shutil.copy2(create_version_only_cli(Path(directory)), bins / 'claude')
            # Both fixtures implement only help/version, never an agent/model call.
            env = {**os.environ, 'PATH': str(bins), 'HOME': directory, 'USERPROFILE': directory, 'LOCALAPPDATA': directory, 'APPDATA': directory, 'PYTHONPATH': '', 'PYTHONHOME': ''}
            process = subprocess.Popen([worker, '--no-browser', '--state-dir', str(state)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd='/' if os.name != 'nt' else directory)
            try:
                ready = queue.Queue()
                threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
                origin = ready.get(timeout=20).strip()
                self.assertRegex(origin, r'^http://127\.0\.0\.1:\d+$')
                def request(route, body=None, csrf=None):
                    headers = {'Origin': origin}
                    if csrf:
                        headers['X-Ustam-CSRF'] = csrf
                    data = None if body is None else json.dumps(body).encode()
                    if data:
                        headers['Content-Type'] = 'application/json'
                    with urllib.request.urlopen(urllib.request.Request(origin+route, data, headers), timeout=30) as response:
                        return json.load(response)
                bootstrap = request('/api/bootstrap')
                self.assertEqual(bootstrap['projects'], [])
                self.assertEqual(set(bootstrap['providers']), {'codex', 'claude', 'opencode', 'antigravity'})
                for asset in ('/', '/app.js', '/style.css'):
                    with urllib.request.urlopen(origin+asset, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                csrf = bootstrap['csrf']
                for provider in ('codex', 'claude', 'opencode', 'antigravity'):
                    project = Path(directory) / provider
                    project.mkdir()
                    request('/api/projects', {'action':'add', 'path':str(project)}, csrf)
                    registered = request('/api/bootstrap')['projects']
                    ident = next(p['id'] for p in registered if p['path'] == str(project.resolve()))
                    model = {'codex':'gpt-6.1-sol', 'claude':'claude-sonnet-5-5', 'opencode':'openai/gpt-6.1-sol', 'antigravity':'pro'}[provider]
                    payload = {'name': 'Offline package check', 'provider': provider, 'chief': {'model': model, 'effort': '' if provider in ('opencode', 'antigravity') else 'medium'},
                               'helpers': [{'id':'one', 'role':'implementer', 'name':'Implementation', 'model':model, 'effort': '' if provider in ('opencode', 'antigravity') else 'medium'}],
                               'concurrency':1, 'profile':'balanced'}
                    preview = request('/api/preview', {'project_ids':[ident], 'provider':provider, 'payload':payload}, csrf)['results'][0]
                    self.assertTrue(preview['ok'], preview)
                    applied = request('/api/apply', {'preview_ids':[preview['preview_id']]}, csrf)['results'][0]
                    self.assertTrue(applied['ok'], applied)
                    self.assertTrue(any(project.iterdir()))
            finally:
                process.terminate()
                process.communicate(timeout=10)

    @unittest.skipUnless(os.environ.get('USTAM_NATIVE_LAUNCHER'), 'Set USTAM_NATIVE_LAUNCHER for native launch smoke')
    def test_native_app_launches_hub_without_picker(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = {**os.environ, 'PATH': '', 'HOME': directory, 'PYTHONHOME':'', 'PYTHONPATH':''}
            process = subprocess.Popen([os.environ['USTAM_NATIVE_LAUNCHER'], '--no-browser', '--port', str(port), '--state-dir', str(Path(directory)/'state')],
                                       start_new_session=os.name != 'nt', env=env, cwd='/' if os.name != 'nt' else directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic()+20
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/bootstrap', timeout=1) as response:
                            bootstrap = json.load(response)
                            self.assertEqual(bootstrap['projects'], [])
                            break
                    except OSError:
                        if process.poll() is not None:
                            self.fail('Native launcher exited before hub readiness')
                        time.sleep(.1)
                else:
                    self.fail('Native launcher never opened hub')
            finally:
                if os.name == 'nt':
                    taskkill = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/taskkill.exe'
                    subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10, check=False)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=10)

if __name__ == '__main__':
    unittest.main()
