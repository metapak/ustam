from __future__ import annotations
import http.client
import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import dashboard

class ConsoleTests(unittest.TestCase):
    def test_managed_write_order_never_duplicates_windows_style_path(self):
        runtime = '.codex\\.bounded-orchestrator\\.gitignore'
        desired = {runtime: b'private', '.codex\\config.toml': b'config'}
        self.assertEqual(dashboard.ordered_managed_paths(desired, runtime),
                         [runtime, '.codex\\config.toml'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name)/'repo'
        self.target.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.target)], check=True)
        self.sessions = Path(self.tmp.name)/'sessions'
        self.sessions.mkdir()
        self.console = dashboard.Console(self.target, self.sessions)

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_codex_config_is_not_an_ustam_installation(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('model = "gpt-6.1-sol"\n')
        self.assertFalse(self.console.settings()['installed'])
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        self.assertTrue(self.console.settings()['installed'])
        self.console.restore({})
        self.assertFalse(self.console.settings()['installed'])
        self.assertTrue(config.exists())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_console_updates_never_take_ownership_of_existing_user_config(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('model = "gpt-6.1-sol"\n[unrelated]\nvalue = 42\n')
        for preset in ('focused', 'balanced'):
            plan = self.console.preview({'preset': preset})
            self.console.save({'preview_id': plan['preview_id']})
            manifest = dashboard.installer.load_manifest(self.target)
            self.assertFalse(manifest['files']['.codex/config.toml']['owned'])
            self.assertEqual(dashboard.tomllib.loads(config.read_text())['unrelated']['value'], 42)
        self.console.restore({})
        self.assertFalse(dashboard.installer.load_manifest(self.target)['files']['.codex/config.toml']['owned'])
        plan = self.console.uninstall_preview({})
        self.assertIn('KEEP .codex/config.toml: pre-existing file was not owned', plan['actions'])
        self.console.uninstall_confirm({'preview_id': plan['preview_id'], 'target': str(self.console.target), 'confirmed': True})
        self.assertEqual(dashboard.tomllib.loads(config.read_text())['unrelated']['value'], 42)
        self.assertFalse(self.console.settings()['installed'])

    def test_preview_save_restore_preserves_other_config_and_files(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        original = '# User comment\nmodel = "gpt-user"\nsecret = "PRIVATE_VALUE"\n\n[unrelated]\nvalue = 3\n'
        config.write_text(original)
        agents = self.target/'AGENTS.md'
        agents.write_text('Personal instruction\n')
        preview = self.console.preview({'preset': 'economy', 'concurrency': 2})
        self.assertTrue(preview['can_save'])
        self.assertEqual(config.read_text(), original)
        self.assertNotIn('PRIVATE_VALUE', json.dumps(preview))
        self.console.save({'preview_id': preview['preview_id']})
        current = config.read_text()
        self.assertIn('secret = "PRIVATE_VALUE"', current)
        self.assertIn('[unrelated]\nvalue = 3', current)
        self.assertEqual(dashboard.tomllib.loads(current)['agents']['max_concurrent_threads_per_session'], 2)
        self.assertNotIn('PRIVATE_VALUE', json.dumps(self.console.settings()))
        self.console.restore({})
        self.assertEqual(config.read_text(), original)
        self.assertEqual(agents.read_text(), 'Personal instruction\n')
        self.assertFalse((self.target/'.codex/tools/usage_report.py').exists())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_after_two_saves_uses_full_manifest(self):
        for preset in ('focused', 'balanced'):
            plan = self.console.preview({'preset': preset})
            self.console.save({'preview_id': plan['preview_id']})
        self.assertTrue((self.target/'.codex/agents/explorer.toml').exists())
        removal = self.console.uninstall_preview({})
        self.assertTrue(any(row == 'REMOVE .codex/agents/explorer.toml' for row in removal['actions']))
        self.assertTrue((self.target/'.codex/agents/explorer.toml').exists())
        with self.assertRaisesRegex(ValueError, 'confirmation'):
            self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': False})
        result = self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertEqual(result['status'], 'uninstalled')
        self.assertFalse((self.target/'.codex/agents/explorer.toml').exists())
        self.assertFalse((self.target/dashboard.installer.MANIFEST_RELATIVE).exists())
        self.assertTrue((self.target/dashboard.STATE).exists())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_preserves_modified_file_and_backup(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('model="gpt-personal"\nprivate_fixture="SECRET_LIKE_TEST_VALUE"\n')
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        role = self.target/'.codex/agents/explorer.toml'
        role.write_text(role.read_text()+'# personal change\n')
        removal = self.console.uninstall_preview({})
        self.assertIn('KEEP .codex/agents/explorer.toml: modified after installation', removal['actions'])
        self.assertIn('KEEP .codex/.bounded-orchestrator/.gitignore: local backups or runtime data remain', removal['actions'])
        self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertIn('# personal change', role.read_text())
        backups = list((self.target/dashboard.installer.BACKUP_RELATIVE).rglob('config.toml'))
        self.assertTrue(backups)
        self.assertTrue((self.target/'.codex/.bounded-orchestrator/.gitignore').exists())
        for backup in backups:
            self.assertIn('SECRET_LIKE_TEST_VALUE', backup.read_text())
            self.assertEqual(subprocess.run(['git', 'check-ignore', str(backup)], cwd=self.target, capture_output=True).returncode, 0)
        subprocess.run(['git', 'add', '-A'], cwd=self.target, check=True)
        self.assertEqual(subprocess.check_output(['git', 'ls-files', '--', '.codex/.bounded-orchestrator/backups'], cwd=self.target), b'')

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_rejects_stale_preview_and_missing_manifest(self):
        with self.assertRaisesRegex(ValueError, 'No install manifest'):
            self.console.uninstall_preview({})
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        (self.target/'.codex/agents/explorer.toml').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'since uninstall preview'):
            self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertTrue((self.target/dashboard.installer.MANIFEST_RELATIVE).exists())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_reports_first_removal_when_later_unlink_fails(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        original_unlink = dashboard.installer.os.unlink
        removed = []

        def fail_after_first(path, *args, **kwargs):
            if '.bounded-uninstall-' not in str(path):
                return original_unlink(path, *args, **kwargs)
            if removed:
                raise OSError('injected second removal failure')
            result = original_unlink(path, *args, **kwargs)
            removed.append(next(row.removeprefix('REMOVE ') for row in removal['actions'] if row.startswith('REMOVE ')))
            return result

        with mock.patch.object(dashboard.installer.os, 'unlink', fail_after_first):
            with self.assertRaises(dashboard.UninstallPartialError) as caught:
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        failure = caught.exception
        self.assertTrue(failure.state_verified)
        self.assertEqual(failure.removed_paths, removed)
        self.assertTrue(failure.manifest_present)
        self.assertFalse(self.target.joinpath(removed[0]).exists())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_rechecks_file_after_hash_before_unlink(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        manifest = dashboard.installer.load_manifest(self.target)
        first = next(name for name, entry in sorted(manifest['files'].items(), reverse=True)
                     if entry.get('owned') and (self.target/name).is_file())
        destination = self.console.target/first
        original_hash = dashboard.installer.sha256_path
        calls = 0

        def change_after_hash(path):
            nonlocal calls
            digest = original_hash(path)
            if path == destination:
                calls += 1
                if calls == 2:
                    path.write_bytes(path.read_bytes() + b'\n# concurrent edit\n')
            return digest

        with mock.patch.object(dashboard.installer, 'sha256_path', side_effect=change_after_hash):
            with self.assertRaises(dashboard.UninstallPartialError) as caught:
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertIn('changed during uninstall', str(caught.exception))
        self.assertEqual(caught.exception.removed_paths, [])
        self.assertTrue(destination.exists())
        self.assertIn(b'concurrent edit', destination.read_bytes())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_rejects_edit_after_final_hash(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        destination = self.console.target/'.codex/tools/work_protocol_core.py'
        original_hash = dashboard.installer.sha256_path
        calls = 0

        def edit_after_hash(path):
            nonlocal calls
            digest = original_hash(path)
            if path == destination:
                calls += 1
                if calls == 3:
                    path.write_bytes(path.read_bytes() + b'\n# concurrent user edit\n')
            return digest

        with mock.patch.object(dashboard.installer, 'sha256_path', side_effect=edit_after_hash):
            with self.assertRaises(dashboard.UninstallPartialError) as caught:
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertEqual(calls, 3)
        self.assertEqual(caught.exception.removed_paths, [])
        self.assertTrue(destination.exists())
        self.assertIn(b'concurrent user edit', destination.read_bytes())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_cannot_follow_redirected_parent_after_final_hash(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        destination = self.console.target/'.codex/tools/usage_report.py'
        outside = Path(self.tmp.name)/'outside'
        outside.mkdir()
        victim = outside/'usage_report.py'
        victim.write_bytes(b'outside user file\n')
        original_hash = dashboard.installer.sha256_path
        calls = 0

        def redirect_after_hash(path):
            nonlocal calls
            digest = original_hash(path)
            if path == destination:
                calls += 1
                if calls == 3:
                    tools = destination.parent
                    tools.rename(tools.with_name('tools-original'))
                    tools.symlink_to(outside, target_is_directory=True)
            return digest

        with mock.patch.object(dashboard.installer, 'sha256_path', side_effect=redirect_after_hash):
            with self.assertRaises(dashboard.UninstallPartialError):
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertEqual(calls, 3)
        self.assertEqual(victim.read_bytes(), b'outside user file\n')
        self.assertTrue(destination.parent.with_name('tools-original').joinpath('usage_report.py').exists())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_restores_file_changed_during_quarantine_rename(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        destination = self.console.target/'.codex/tools/work_protocol_core.py'
        original_rename = dashboard.installer.os.rename
        changed = False

        def edit_before_stage(source, destination_name, *args, **kwargs):
            nonlocal changed
            if source == destination.name and not changed:
                destination.write_bytes(destination.read_bytes() + b'\n# stage-time edit\n')
                changed = True
            return original_rename(source, destination_name, *args, **kwargs)

        with mock.patch.object(dashboard.installer.os, 'rename', edit_before_stage):
            with self.assertRaises(dashboard.UninstallPartialError) as caught:
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertTrue(changed, str(caught.exception))
        self.assertEqual(caught.exception.removed_paths, [])
        self.assertTrue(destination.exists())
        self.assertIn(b'stage-time edit', destination.read_bytes())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_rejects_file_appearing_after_approved_actions(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        destination = self.console.target/'.codex/tools/usage_report.py'
        destination.unlink()
        removal = self.console.uninstall_preview({})
        self.assertNotIn('REMOVE .codex/tools/usage_report.py', removal['actions'])
        original_uninstall = dashboard.installer.uninstall
        inserted = False

        def appear_after_check(target, dry_run, expected_files=None):
            nonlocal inserted
            result = original_uninstall(target, dry_run, expected_files)
            if dry_run and not inserted:
                destination.write_bytes(b'new user file after preview\n')
                inserted = True
            return result

        with mock.patch.object(dashboard.installer, 'uninstall', side_effect=appear_after_check):
            with self.assertRaises(dashboard.UninstallPartialError) as caught:
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertTrue(inserted)
        self.assertEqual(caught.exception.removed_paths, [])
        self.assertEqual(destination.read_bytes(), b'new user file after preview\n')

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_recovers_edit_at_final_file_unlink(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        destination = self.console.target/'.codex/tools/usage_report.py'
        original_unlink = dashboard.installer.os.unlink
        injected = False

        def edit_before_unlink(name, *args, **kwargs):
            nonlocal injected
            if str(name).startswith('.usage_report.py.bounded-uninstall-') and not injected:
                fd = dashboard.installer.os.open(name, dashboard.installer.os.O_WRONLY | dashboard.installer.os.O_APPEND,
                                                  dir_fd=kwargs['dir_fd'])
                try:
                    dashboard.installer.os.write(fd, b'\n# final user edit\n')
                finally:
                    dashboard.installer.os.close(fd)
                injected = True
            return original_unlink(name, *args, **kwargs)

        with mock.patch.object(dashboard.installer.os, 'unlink', edit_before_unlink):
            with self.assertRaises(dashboard.UninstallPartialError):
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertTrue(injected)
        backups = list((self.console.target/dashboard.installer.BACKUP_RELATIVE).rglob('*usage_report.py'))
        self.assertTrue(any(b'final user edit' in path.read_bytes() for path in backups))
        self.assertTrue(destination.exists())
        self.assertIn(b'final user edit', destination.read_bytes())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_recovers_agents_edit_at_final_replace(self):
        agents = self.console.target/'AGENTS.md'
        agents.write_text('Personal intro\n')
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        original_unlink = dashboard.installer.os.unlink
        injected = False

        def edit_before_unlink(name, *args, **kwargs):
            nonlocal injected
            if str(name).startswith('.AGENTS.md.bounded-uninstall-') and not injected:
                fd = dashboard.installer.os.open(name, dashboard.installer.os.O_WRONLY | dashboard.installer.os.O_APPEND,
                                                  dir_fd=kwargs['dir_fd'])
                try:
                    dashboard.installer.os.write(fd, b'\nPersonal last-minute instruction\n')
                finally:
                    dashboard.installer.os.close(fd)
                injected = True
            return original_unlink(name, *args, **kwargs)

        with mock.patch.object(dashboard.installer.os, 'unlink', edit_before_unlink):
            with self.assertRaises(dashboard.UninstallPartialError) as caught:
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertTrue(injected)
        backups = list((self.console.target/dashboard.installer.BACKUP_RELATIVE).rglob('AGENTS.md'))
        self.assertTrue(any(b'Personal last-minute instruction' in path.read_bytes() for path in backups))
        self.assertIn('rollback incomplete', str(caught.exception))
        self.assertTrue(agents.exists())

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_browser_uninstall_keeps_both_agents_edits_during_rollback(self):
        agents = self.console.target/'AGENTS.md'
        agents.write_text('Personal intro\n')
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        removal = self.console.uninstall_preview({})
        original_unlink = dashboard.installer.os.unlink
        original_edit = replacement_edit = atomic_save = False

        def edit_both_versions(name, *args, **kwargs):
            nonlocal original_edit, replacement_edit, atomic_save
            if name == 'AGENTS.md':
                self.fail('rollback must not unlink the current AGENTS.md path')
            if str(name).startswith('.AGENTS.md.bounded-uninstall-') and not original_edit:
                marker = b'\nOriginal last-minute edit\n'
                original_edit = True
                replacement_fd = dashboard.installer.os.open('AGENTS.md', dashboard.installer.os.O_WRONLY | dashboard.installer.os.O_APPEND,
                                                              dir_fd=kwargs['dir_fd'])
                try:
                    dashboard.installer.os.write(replacement_fd, b'\nReplacement last-minute edit\n')
                finally:
                    dashboard.installer.os.close(replacement_fd)
                replacement_edit = True
                temporary = agents.with_name('AGENTS.md.concurrent')
                temporary.write_bytes(b'Atomic saved personal instruction\n')
                dashboard.installer.os.replace(temporary, agents)
                atomic_save = True
            else:
                marker = None
            if marker:
                fd = dashboard.installer.os.open(name, dashboard.installer.os.O_WRONLY | dashboard.installer.os.O_APPEND,
                                                  dir_fd=kwargs['dir_fd'])
                try:
                    dashboard.installer.os.write(fd, marker)
                finally:
                    dashboard.installer.os.close(fd)
            return original_unlink(name, *args, **kwargs)

        with mock.patch.object(dashboard.installer.os, 'unlink', edit_both_versions):
            with self.assertRaises(dashboard.UninstallPartialError) as caught:
                self.console.uninstall_confirm({'preview_id': removal['preview_id'], 'target': removal['target'], 'confirmed': True})
        self.assertTrue(original_edit and replacement_edit and atomic_save)
        self.assertEqual(agents.read_bytes(), b'Atomic saved personal instruction\n')
        original_backups = list((self.console.target/dashboard.installer.BACKUP_RELATIVE).rglob('AGENTS.md'))
        self.assertTrue(any(b'Original last-minute edit' in path.read_bytes() for path in original_backups))
        backups = list((self.console.target/dashboard.installer.BACKUP_RELATIVE).rglob('AGENTS.md.replacement'))
        self.assertTrue(any(b'Replacement last-minute edit' in path.read_bytes() for path in backups))
        self.assertIn('rollback incomplete', str(caught.exception))

    def test_browser_uninstall_preview_refuses_unsupported_platform(self):
        plan = self.console.preview({'preset': 'focused'})
        self.console.save({'preview_id': plan['preview_id']})
        with mock.patch.object(dashboard.installer, 'SAFE_UNINSTALL_SUPPORTED', False):
            self.assertFalse(self.console.settings()['uninstall_available'])
            with self.assertRaisesRegex(ValueError, 'requires POSIX'):
                self.console.uninstall_preview({})

    def test_crlf_preview_hides_unrelated_secret_and_save_preserves_bytes(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        unrelated = b'secret = "PRIVATE_VALUE"\r\n\r\n[unrelated]\r\nnote = "keep"\r\n'
        original = b'model = "gpt-user"\r\n' + unrelated
        config.write_bytes(original)
        preview = self.console.preview({'preset': 'focused'})
        self.assertTrue(preview['can_save'])
        self.assertNotIn('PRIVATE_VALUE', json.dumps(preview))
        self.assertIn('model:', json.dumps(preview))
        self.assertEqual(config.read_bytes(), original)
        self.console.save({'preview_id': preview['preview_id']})
        saved = config.read_bytes()
        self.assertIn(b'secret = "PRIVATE_VALUE"\r\n', saved)
        self.assertIn(b'[unrelated]\r\nnote = "keep"\r\n', saved)
        self.assertNotIn(b'\n', saved.replace(b'\r\n', b''))
        self.console.restore({})
        self.assertEqual(config.read_bytes(), original)

    def test_array_tables_and_multiline_strings_preserve_unrelated_fields(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        triple = '"' * 3
        original = ('note = '+triple+'\nmodel = "string-data"\n[agents]\nmax_depth = 999\n'+triple+'\n'
                    '[[unrelated]]\nmodel = "private-provider"\nsecret = "keep"\n'
                    '  ["quoted]table"]\nmodel = "other-provider"\n')
        config.write_text(original)
        before = dashboard.tomllib.loads(original)
        preview = self.console.preview({'preset': 'focused'})
        self.assertTrue(preview['can_save'])
        self.console.save({'preview_id':preview['preview_id']})
        current = dashboard.tomllib.loads(config.read_text())
        self.assertEqual(current['unrelated'], before['unrelated'])
        self.assertEqual(current['quoted]table'], before['quoted]table'])
        self.assertEqual(current['note'], before['note'])
        self.assertEqual(current['model'], 'gpt-6.1-sol')
        self.assertEqual(current['agents']['max_depth'], 1)
        self.console.restore({})
        self.assertEqual(config.read_text(), original)

    def test_malformed_toml_refused_before_patching_selected_keys(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('model = invalid\n')
        with self.assertRaises(dashboard.tomllib.TOMLDecodeError):
            self.console.preview({})
        self.assertEqual(config.read_text(), 'model = invalid\n')

    def test_first_install_restore_keeps_backups_ignored_and_unstaged(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        original = 'model="gpt-personal"\nprivate_fixture="SECRET_LIKE_TEST_VALUE"\n'
        config.write_text(original)
        plan = self.console.preview({'preset':'focused'})
        self.console.save({'preview_id':plan['preview_id']})
        backups = list((self.target/dashboard.installer.BACKUP_RELATIVE).rglob('config.toml'))
        self.assertTrue(backups)
        self.console.restore({})
        self.assertEqual(config.read_text(), original)
        ignore = self.target/'.codex/.bounded-orchestrator/.gitignore'
        self.assertEqual(ignore.read_text(), '*\n!.gitignore\n')
        for backup in backups:
            checked = subprocess.run(['git','check-ignore',str(backup)],cwd=self.target,capture_output=True)
            self.assertEqual(checked.returncode,0)
            self.assertIn('SECRET_LIKE_TEST_VALUE',backup.read_text())
        subprocess.run(['git','add','-A'],cwd=self.target,check=True)
        staged = subprocess.check_output(['git','ls-files','--','.codex/.bounded-orchestrator/backups'],cwd=self.target)
        self.assertEqual(staged,b'')

    def test_preview_conflict_and_stale_save(self):
        role = self.target/'.codex/agents/explorer.toml'
        role.parent.mkdir(parents=True)
        role.write_text('model="gpt-personal"\nmodel_reasoning_effort="low"\n')
        plan = self.console.preview({'preset': 'balanced'})
        self.assertIn('.codex/agents/explorer.toml', [path.replace('\\', '/') for path in plan['conflicts']])
        with self.assertRaises(ValueError):
            self.console.save({'preview_id': plan['preview_id']})
        role.unlink()
        plan = self.console.preview({})
        (self.target/'AGENTS.md').write_text('Changed concurrently\n')
        with self.assertRaisesRegex(ValueError, 'since preview'):
            self.console.save({'preview_id': plan['preview_id']})

    def test_restore_refuses_modified_saved_files(self):
        preview = self.console.preview({})
        self.console.save({'preview_id': preview['preview_id']})
        (self.target/'.codex/config.toml').write_text('model="gpt-other"\n')
        with self.assertRaisesRegex(ValueError, 'changed after Save'):
            self.console.restore({})

    def test_validation_and_symlink(self):
        for payload in ({'concurrency': 0}, {'concurrency': True}, {'preset':'missing'}, {'roles':{'unknown':{}}}, {'roles':{'owner':{'model':'claude-5','effort':'low'}}}, {'roles':{'owner':{'model':'gpt-6-astra','effort':'none'}}}):
            with self.assertRaises((ValueError, dashboard.installer.InstallError)):
                self.console.preview(payload)
        (self.target/'.codex').symlink_to(self.sessions, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.console.preview({})

    def test_usage_identifies_sanitized_sample_and_selected_source(self):
        sample = ROOT/'tests/fixtures/usage-sanitized'
        console = dashboard.Console(self.target, sample)
        report = console.report({})
        self.assertTrue(report['sample_data'])
        self.assertEqual(report['source_path'], str(sample.resolve()))
        self.assertEqual(report['totals']['total_tokens'], 88194)
        self.assertEqual({row['id']:row['total_tokens'] for row in report['breakdowns']['model']['rows']}, {'gpt-6-sol':35268, 'gpt-6-astra':32426, 'gpt-6-luna':12000, 'gpt-5.6-sol':8000, 'unknown':500})
        self.assertEqual({row['id']:row['total_tokens'] for row in report['breakdowns']['style']['rows']}, {'focused':29268, 'quality':58426, 'unknown':500})
        self.assertEqual(report['orchestra']['conductor_tokens'],61694)
        self.assertEqual(report['orchestra']['helper_tokens'],26000)
        self.assertEqual(report['orchestra']['unassigned_tokens'],500)
        self.assertEqual(report['orchestra']['helper_count'],3)
        scoped = console.report({'root':['demo-root']})
        self.assertEqual(scoped['orchestra']['helper_count'],4)
        self.assertIsNone(next(actor['total_tokens'] for actor in scoped['orchestra']['actors'] if actor['id']=='demo-helper-four'))
        self.assertEqual(sum(scoped['orchestra'][key] for key in ('conductor_tokens','helper_tokens','unassigned_tokens')),scoped['orchestra']['total_tokens'])
        filtered = console.report({'date_from':['2026-09-02'], 'date_to':['2026-09-02'], 'thread':['demo-root'], 'root':['demo-root']})
        self.assertEqual(filtered['breakdowns']['total_tokens'], 32426)
        self.assertEqual(filtered['breakdowns']['model']['rows'], [{'id':'gpt-6-astra','total_tokens':32426}])
        self.assertEqual(filtered['breakdowns']['style']['rows'], [{'id':'quality','total_tokens':32426}])
        self.assertEqual(filtered['orchestra']['helper_count'],0)
        self.assertFalse(self.console.report({})['sample_data'])

    def test_time_strip_uses_deduped_cumulative_deltas_and_keeps_unknown_time(self):
        rows = [{'timestamp':'2026-09-01T09:00:00Z','type':'session_meta','payload':{'id':'legacy-thread','cwd':str(self.target),'source':'cli'}}]
        rows += [{'timestamp':stamp,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}}}}
                 for stamp,total in [('2026-09-01T10:00:00Z',12),('2026-09-02T10:00:00Z',19),
                                     ('2026-09-02T11:00:00Z',4),('2026-09-02T12:00:00Z',9)]]
        for name in ('a.jsonl','duplicate.jsonl'):
            (self.sessions/name).write_text(''.join(json.dumps(row)+'\n' for row in rows))
        (self.sessions/'unknown.jsonl').write_text(json.dumps({'type':'token_usage_record','thread_id':'other-thread','usage':{'total_tokens':3}})+'\n')
        report = self.console.report({})
        timeline = report['time_breakdown']
        self.assertEqual(report['totals']['total_tokens'],31)
        self.assertEqual(report['counter_resets'],1)
        self.assertEqual(timeline['days'],[{'day':'2026-09-01','total_tokens':12},{'day':'2026-09-02','total_tokens':16}])
        self.assertEqual(timeline['unknown_time_tokens'],3)
        self.assertEqual(sum(row['total_tokens'] for row in timeline['days'])+timeline['unknown_time_tokens'],timeline['total_tokens'])
        filtered = self.console.report({'date_from':['2026-09-02']})['time_breakdown']
        self.assertEqual(filtered['days'],[{'day':'2026-09-02','total_tokens':16}])
        self.assertEqual(filtered['unknown_time_tokens'],0)

    def test_style_attribution_requires_bounded_turn_and_exact_project(self):
        rows = [
            {'model':'gpt-one','project':str(self.target),'turn_start':'2026-09-01T10:00:00Z','turn_end':'2026-09-01T10:01:00Z','usage':{'input_tokens':15,'cached_input_tokens':10,'output_tokens':5,'total_tokens':20}},
            {'model':'unknown','project':str(self.target),'turn_start':'2026-09-01T10:30:00Z','turn_end':'2026-09-01T11:01:00Z','usage':{'total_tokens':10}},
            {'model':'gpt-two','project':str(self.target),'turn_start':'unknown','turn_end':'2026-09-01T12:00:00Z','usage':{'total_tokens':5}},
            {'model':'gpt-two','project':'/other/project','turn_start':'2026-09-01T12:00:00Z','turn_end':'2026-09-01T12:01:00Z','usage':{'total_tokens':15}},
        ]
        events = [{'at':'2026-09-01T09:00:00Z','action':'save','preset':'focused'}, {'at':'2026-09-01T11:00:00Z','action':'restore'}]
        result = dashboard.usage_breakdowns({'records':rows,'totals':{'total_tokens':50}},events,str(self.target))
        self.assertEqual(sum(row['total_tokens'] for row in result['model']['rows']),50)
        self.assertEqual(sum(row['total_tokens'] for row in result['style']['rows']),50)
        self.assertEqual({row['id']:row['total_tokens'] for row in result['style']['rows']},{'focused':20,'unknown':30})
        self.assertEqual(next(row['total_tokens'] for row in result['model']['rows'] if row['id']=='unknown'),10)

    def test_save_restore_writes_private_style_transitions(self):
        plan = self.console.preview({'preset':'quality'})
        self.console.save({'preview_id':plan['preview_id']})
        history = self.console.style_history()
        self.assertEqual(history[-1]['action'],'save')
        self.assertEqual(history[-1]['preset'],'quality')
        self.assertEqual(history[-1]['roles']['owner']['model'],'gpt-6-astra')
        ignored = subprocess.run(['git','check-ignore',str(self.target/dashboard.HISTORY)],cwd=self.target,capture_output=True)
        self.assertEqual(ignored.returncode,0)
        self.console.restore({})
        self.assertEqual(self.console.style_history()[-1]['action'],'restore')

    def test_unfinished_turn_is_unknown_until_matching_task_complete_marker(self):
        history = self.target/dashboard.HISTORY
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps({'schema':1,'events':[{'at':'2026-09-01T09:00:00Z','action':'save','preset':'focused'}]}))
        log = self.sessions/'rollout.jsonl'
        rows = [
            {'timestamp':'2026-09-01T10:00:00Z','type':'session_meta','payload':{'id':'session-one','cwd':str(self.target)}},
            {'timestamp':'2026-09-01T10:01:00Z','type':'turn_context','payload':{'turn_id':'turn-one','model':'gpt-6.1-sol'}},
            {'timestamp':'2026-09-01T10:02:00Z','type':'token_usage_record','payload':{'turn_id':'turn-one','usage':{'total_tokens':25}}},
        ]
        log.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.assertEqual(self.console.report({})['breakdowns']['style']['rows'],[{'id':'unknown','total_tokens':25}])
        rows.append({'timestamp':'2026-09-01T10:03:00Z','type':'event_msg','payload':{'type':'task_complete','turn_id':'turn-one'}})
        log.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.assertEqual(self.console.report({})['breakdowns']['style']['rows'],[{'id':'focused','total_tokens':25}])

    def test_same_session_turns_split_across_saves_and_ambiguous_turns_unknown(self):
        history = self.target/dashboard.HISTORY
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps({'schema':1,'events':[
            {'at':'2026-09-01T09:00:00Z','action':'save','preset':'focused'},
            {'at':'2026-09-01T11:00:00Z','action':'save','preset':'quality'},
        ]}))
        rows = [{'timestamp':'2026-09-01T08:00:00Z','type':'session_meta','payload':{'id':'shared-session','cwd':str(self.target)}}]
        def turn(turn_id, start, usage_at, end, model, tokens):
            rows.extend([
                {'timestamp':start,'type':'turn_context','payload':{'turn_id':turn_id,'model':model}},
                {'timestamp':usage_at,'type':'token_usage_record','payload':{'turn_id':turn_id,'usage':{'input_tokens':tokens-1,'cached_input_tokens':1,'output_tokens':1,'total_tokens':tokens}}},
            ])
            if end:
                rows.append({'timestamp':end,'type':'event_msg','payload':{'type':'task_complete','turn_id':turn_id}})
        turn('turn-focused','2026-09-01T10:00:00Z','2026-09-01T10:01:00Z','2026-09-01T10:05:00Z','gpt-6-sol',10)
        turn('turn-crossing','2026-09-01T10:59:00Z','2026-09-01T11:00:00Z','2026-09-01T11:01:00Z','gpt-6-sol',7)
        turn('turn-quality','2026-09-01T11:10:00Z','2026-09-01T11:11:00Z','2026-09-01T11:15:00Z','gpt-6-astra',20)
        turn('turn-open','2026-09-01T11:20:00Z','2026-09-01T11:21:00Z',None,'gpt-6-astra',5)
        (self.sessions/'rollout.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        report = self.console.report({})
        self.assertEqual(report['totals']['total_tokens'],42)
        self.assertEqual({row['id']:row['total_tokens'] for row in report['breakdowns']['style']['rows']},{'focused':10,'quality':20,'unknown':12})
        self.assertEqual(sum(row['total_tokens'] for row in report['breakdowns']['model']['rows']),42)

    def test_agent_hierarchy_nested_orphan_cycle_and_multiple_models(self):
        agents = [
            {'id':'root','parent':None,'source':'root','name':'','role':'owner','project':'/project'},
            {'id':'child','parent':'root','source':'subagent','name':'<script>not markup</script>','role':'researcher','project':'/project'},
            {'id':'grandchild','parent':'child','source':'subagent','name':'','role':'reviewer','project':'/project'},
            {'id':'orphan','parent':'missing','source':'subagent','name':'','role':'worker','project':'/project'},
            {'id':'bad-source','parent':'root','source':'root','name':'','role':'worker','project':'/project'},
            {'id':'cycle-a','parent':'cycle-b','source':'subagent','name':'','role':'worker','project':'/project'},
            {'id':'cycle-b','parent':'cycle-a','source':'subagent','name':'','role':'worker','project':'/project'},
        ]
        records = [
            {'thread':'root','session_id':'root','model':'gpt-root','timestamp':'2026-09-01T10:00:00Z','usage':{'total_tokens':10}},
            {'thread':'child','session_id':'root','model':'gpt-one','timestamp':'2026-09-01T10:01:00Z','usage':{'total_tokens':20}},
            {'thread':'child','session_id':'root','model':'gpt-two','timestamp':'2026-09-01T10:02:00Z','usage':{'total_tokens':30}},
            {'thread':'grandchild','session_id':'root','model':'gpt-two','timestamp':'2026-09-01T10:03:00Z','usage':{'total_tokens':5}},
            {'thread':'orphan','session_id':'root','model':'gpt-one','timestamp':'2026-09-01T10:04:00Z','usage':{'total_tokens':7}},
            {'thread':'bad-source','session_id':'root','model':'gpt-one','timestamp':'2026-09-01T10:04:01Z','usage':{'total_tokens':9}},
            {'thread':'cycle-a','session_id':'root','model':'gpt-one','timestamp':'2026-09-01T10:05:00Z','usage':{'total_tokens':3}},
        ]
        result = dashboard.orchestra_view({'agents':agents,'records':records},'root')
        self.assertEqual((result['total_tokens'],result['conductor_tokens'],result['helper_tokens'],result['unassigned_tokens']),(84,10,55,19))
        self.assertEqual(result['helper_count'],2)
        child = next(actor for actor in result['actors'] if actor['id']=='child')
        self.assertEqual(child['name'],'<script>not markup</script>')
        self.assertEqual(child['models'],[{'id':'gpt-two','total_tokens':30},{'id':'gpt-one','total_tokens':20}])

    def test_conflicting_agent_metadata_across_files_remains_unassigned(self):
        root_one = {'type':'session_meta','payload':{'id':'root-one','source':'cli','cwd':'/project'}}
        root_two = {'type':'session_meta','payload':{'id':'root-two','source':'cli','cwd':'/project'}}
        first = {'type':'session_meta','payload':{'id':'same-helper','parent_thread_id':'root-one','source':{'subagent':{}},'agent_role':'reviewer','cwd':'/project'}}
        second = {'type':'session_meta','payload':{'id':'same-helper','parent_thread_id':'root-two','source':{'subagent':{}},'agent_role':'reviewer','cwd':'/project'}}
        usage_row = {'timestamp':'2026-09-01T10:00:00Z','type':'token_usage_record','payload':{'thread_id':'same-helper','session_id':'root-one','usage':{'total_tokens':9}}}
        for filename, rows in [('a.jsonl',[root_one,first,usage_row]),('b.jsonl',[root_two,second])]:
            (self.sessions/filename).write_text(''.join(json.dumps(row)+'\n' for row in rows))
        report = dashboard.usage.scan(self.sessions)
        self.assertTrue(next(agent for agent in report['agents'] if agent['id']=='same-helper')['ambiguous'])
        result = dashboard.orchestra_view(report,'root-one')
        self.assertEqual((result['helper_count'],result['helper_tokens'],result['unassigned_tokens']),(0,0,9))

    def test_model_catalog_merges_runtime_and_offline_documentation(self):
        import model_catalog
        with mock.patch.object(model_catalog, 'discover_cli', return_value=[{'id':'gpt-6-astra','label':'GPT-6 Astra','efforts':['low','medium'],'origin':'local_cli'}]):
            catalog = model_catalog.catalog()
        entries = {item['id']:item for item in catalog['models']}
        self.assertEqual(catalog['discovery'], 'local_cli')
        self.assertEqual(entries['gpt-6-astra']['origin'], 'local_cli')
        self.assertEqual(entries['gpt-6.1-sol']['origin'], 'documentation')
        self.assertEqual(entries['gpt-6.1-sol']['efforts'], ['low','medium','high','xhigh','max','ultra'])
        self.assertFalse(catalog['account_access_verified'])
        with mock.patch.object(model_catalog, 'discover_cli', return_value=None):
            fallback = model_catalog.catalog()
        self.assertEqual(fallback['discovery'], 'documentation_fallback')
        self.assertTrue(fallback['documentation_reviewed_at'])
        self.assertIn('gpt-6-luna', {item['id'] for item in fallback['models']})
        self.assertIn('ultra', next(item for item in fallback['models'] if item['id']=='gpt-6.1-sol')['efforts'])

    def test_new_model_choices_hide_legacy_but_allow_newer_local_revision(self):
        import model_catalog
        local = [{'id':name,'label':name,'efforts':['medium'],'origin':'local_cli'}
                 for name in ('gpt-5.6-sol','gpt-6-sol','gpt-6.2-sol')]
        with mock.patch.object(model_catalog, 'discover_cli', return_value=local):
            result = model_catalog.catalog()
        entries = {item['id']:item for item in result['models']}
        self.assertEqual(set(entries), {'gpt-6-astra','gpt-6.1-sol','gpt-6-luna','gpt-6.2-sol'})
        self.assertEqual(entries['gpt-6.2-sol']['origin'], 'local_cli')
        self.assertFalse(result['account_access_verified'])

    def test_advisory_status_exposes_names_only_not_bridge_credentials(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('[mcp_servers.anthropic_claude]\ncommand = "python"\n'
                          'args = ["private-script", "PRIVATE_KEY"]\n'
                          '[mcp_servers.deepseek_proposals]\nenabled = false\n')
        state = self.console.settings()
        self.assertEqual(state['advisory'], {'anthropic': True, 'deepseek': False})
        self.assertNotIn('PRIVATE_KEY', json.dumps(state))

    def test_adviser_preview_save_switch_remove_and_restore(self):
        anthropic = {'provider':'anthropic','model':'claude-sonnet-5-5','effort':'high'}
        deepseek = {'provider':'deepseek','model':'deepseek-flash','effort':'low'}
        first = self.console.preview({'preset':'focused','advisory':anthropic})
        self.assertTrue(first['can_save'], first['conflicts'])
        self.assertIn('adviser: none → anthropic', '\n'.join(item['diff'] for item in first['changes']))
        self.assertNotIn('API_KEY=', json.dumps(first))
        self.console.save({'preview_id':first['preview_id']})
        self.assertEqual(self.console.settings()['advisory_selection'], anthropic)
        config = dashboard.tomllib.loads((self.target/dashboard.installer.CONFIG_RELATIVE).read_text())
        self.assertEqual(config['mcp_servers']['anthropic_claude']['env_vars'], ['ANTHROPIC_API_KEY'])
        self.assertIn('claude-sonnet-5-5', config['mcp_servers']['anthropic_claude']['args'])
        self.assertTrue((self.target/dashboard.installer.ANTHROPIC_BRIDGE_RELATIVE).is_file())
        second = self.console.preview({'preset':'focused','advisory':deepseek})
        self.assertTrue(second['can_save'], second['conflicts'])
        self.console.save({'preview_id':second['preview_id']})
        self.assertEqual(self.console.settings()['advisory_selection'], deepseek)
        self.assertFalse((self.target/dashboard.installer.ANTHROPIC_BRIDGE_RELATIVE).exists())
        self.assertTrue((self.target/dashboard.installer.DEEPSEEK_BRIDGE_RELATIVE).is_file())
        third = self.console.preview({'preset':'focused','advisory':{'provider':'none','model':'','effort':''}})
        self.console.save({'preview_id':third['preview_id']})
        self.assertEqual(self.console.settings()['advisory_selection']['provider'], 'none')
        self.assertFalse((self.target/dashboard.installer.DEEPSEEK_BRIDGE_RELATIVE).exists())
        self.console.restore({})
        self.assertEqual(self.console.settings()['advisory_selection'], deepseek)
        self.assertTrue((self.target/dashboard.installer.DEEPSEEK_BRIDGE_RELATIVE).is_file())

    def test_adviser_exact_allowlist_and_haiku_auto(self):
        for provider, model, effort in [('anthropic','claude-opus-5-5','high'),
                                         ('anthropic','claude-fable-5-1','max'),
                                         ('anthropic','claude-haiku-4-5-20251001','auto'),
                                         ('deepseek','deepseek-flash','none')]:
            with self.subTest(model=model):
                plan = self.console.preview({'preset':'focused','advisory':{'provider':provider,'model':model,'effort':effort}})
                self.assertTrue(plan['can_save'])
        for provider, model, effort in [('anthropic','claude-haiku-4-5-20251001','high'),
                                         ('anthropic','claude-opus-5-5','auto'),
                                         ('deepseek','deepseek-flash','medium'),
                                         ('anthropic','claude-unlisted','high')]:
            with self.subTest(model=model, effort=effort), self.assertRaisesRegex(ValueError, 'Unsupported adviser'):
                self.console.preview({'preset':'focused','advisory':{'provider':provider,'model':model,'effort':effort}})

    def test_adviser_user_changed_table_refused_and_unrelated_root_retained(self):
        choice = {'provider':'anthropic','model':'claude-sonnet-5-5','effort':'high'}
        first = self.console.preview({'preset':'focused','advisory':choice})
        self.console.save({'preview_id':first['preview_id']})
        path = self.target/dashboard.installer.CONFIG_RELATIVE
        path.write_text('private_setting = "KEEP"\n'+path.read_text())
        next_choice = {'provider':'deepseek','model':'deepseek-flash','effort':'high'}
        plan = self.console.preview({'preset':'focused','advisory':next_choice})
        self.assertTrue(plan['can_save'])
        self.console.save({'preview_id':plan['preview_id']})
        self.assertIn('private_setting = "KEEP"', path.read_text())
        path.write_text(path.read_text().replace('enabled = true', 'enabled = false'))
        with self.assertRaisesRegex(ValueError, 'adviser configuration was changed'):
            self.console.preview({'preset':'focused','advisory':choice})

    def test_adviser_switch_refuses_comments_without_changing_files(self):
        choice = {'provider':'anthropic','model':'claude-sonnet-5-5','effort':'high'}
        switched = {'provider':'deepseek','model':'deepseek-flash','effort':'low'}
        first = self.console.preview({'preset':'focused','advisory':choice})
        self.console.save({'preview_id':first['preview_id']})
        pending = self.console.preview({'preset':'focused','advisory':switched})
        path = self.target/dashboard.installer.CONFIG_RELATIVE
        with path.open('ab') as config:
            config.write(b'\n# PERSONAL COMMENT KEEP\n[unrelated]\nnote = "KEEP"\n')
        before = self.console.snapshot()
        with self.assertRaisesRegex(ValueError, 'since preview'):
            self.console.save({'preview_id':pending['preview_id']})
        self.assertEqual(self.console.snapshot(), before)
        with self.assertRaisesRegex(ValueError, 'personal comments'):
            self.console.preview({'preset':'focused','advisory':switched})
        self.assertEqual(self.console.snapshot(), before)
        self.assertIn(b'# PERSONAL COMMENT KEEP', path.read_bytes())
        self.assertIn(b'note = "KEEP"', path.read_bytes())

    def test_adviser_switch_refuses_inline_comment_without_changing_files(self):
        choice = {'provider':'anthropic','model':'claude-sonnet-5-5','effort':'high'}
        switched = {'provider':'deepseek','model':'deepseek-flash','effort':'low'}
        first = self.console.preview({'preset':'focused','advisory':choice})
        self.console.save({'preview_id':first['preview_id']})
        path = self.target/dashboard.installer.CONFIG_RELATIVE
        before_text = path.read_bytes()
        self.assertIn(b'enabled = true', before_text)
        prefix, enabled = before_text.rsplit(b'enabled = true', 1)
        path.write_bytes(prefix + b'enabled = true # PERSONAL_INLINE_COMMENT_KEEP' + enabled)
        before = self.console.snapshot()
        with self.assertRaisesRegex(ValueError, 'personal comments'):
            self.console.preview({'preset':'focused','advisory':switched})
        self.assertEqual(self.console.snapshot(), before)
        self.assertIn(b'PERSONAL_INLINE_COMMENT_KEEP', path.read_bytes())
        self.assertEqual(dashboard.without_advisory_tables('[mcp_servers.anthropic_claude]\nargs = ["a#b"]\n'), '')

    def test_adviser_stale_symlink_and_midwrite_rollback(self):
        choice = {'provider':'anthropic','model':'claude-sonnet-5-5','effort':'high'}
        plan = self.console.preview({'preset':'focused','advisory':choice})
        path = self.target/'AGENTS.md'
        path.write_text('OWNER EDIT\n')
        with self.assertRaisesRegex(ValueError, 'since preview'):
            self.console.save({'preview_id':plan['preview_id']})
        plan = self.console.preview({'preset':'focused','advisory':choice})
        self.console.save({'preview_id':plan['preview_id']})
        new_choice = {'provider':'deepseek','model':'deepseek-flash','effort':'high'}
        before = self.console.snapshot()
        plan = self.console.preview({'preset':'focused','advisory':new_choice})
        original = dashboard.installer.install_text_file
        def fail_bridge(**kwargs):
            if kwargs.get('relative') == dashboard.installer.DEEPSEEK_BRIDGE_RELATIVE:
                raise OSError('injected bridge write failure')
            return original(**kwargs)
        with mock.patch.object(dashboard.installer, 'install_text_file', side_effect=fail_bridge):
            with self.assertRaisesRegex(OSError, 'injected bridge write failure'):
                self.console.save({'preview_id':plan['preview_id']})
        self.assertEqual(self.console.snapshot(), before)
        outside = Path(self.tmp.name)/'outside.txt'
        outside.write_text('UNCHANGED')
        bridge = self.target/dashboard.installer.DEEPSEEK_BRIDGE_RELATIVE
        bridge.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.console.preview({'preset':'focused','advisory':new_choice})
        self.assertEqual(outside.read_text(), 'UNCHANGED')

    def test_cli_installed_older_adviser_model_survives_unrelated_console_save(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/install.py'), str(self.target),
                                 '--preset', 'focused', '--external-provider', 'anthropic',
                                 '--external-model', 'claude-sonnet-5', '--external-effort', 'high'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = {'provider':'anthropic','model':'claude-sonnet-5','effort':'high'}
        self.assertEqual(self.console.settings()['advisory_selection'], saved)
        plan = self.console.preview({'preset':'quality'})
        self.assertTrue(plan['can_save'], plan['conflicts'])
        self.console.save({'preview_id':plan['preview_id']})
        self.assertEqual(self.console.settings()['advisory_selection'], saved)

    def test_local_gpt_6_1_efforts_override_documentation_fallback(self):
        import model_catalog
        local = [{'id':'gpt-6.1-sol','label':'GPT-6.1 Sol','efforts':['low','medium','high','xhigh','max'],'origin':'local_cli'}]
        with mock.patch.object(model_catalog, 'discover_cli', return_value=local):
            self.console._catalog = model_catalog.catalog()
        entry = next(item for item in self.console._catalog['models'] if item['id']=='gpt-6.1-sol')
        self.assertEqual(entry['origin'], 'local_cli')
        self.assertNotIn('ultra', entry['efforts'])
        with self.assertRaisesRegex(ValueError, 'reasoning effort is not supported'):
            self.console.preview({'preset':'balanced', 'roles':{'owner':{'model':'gpt-6.1-sol','effort':'ultra'}}})
        self.assertTrue(self.console.preview({'preset':'balanced', 'roles':{'owner':{'model':'gpt-6.1-sol','effort':'max'}}})['can_save'])

    def test_saved_unlisted_model_is_preserved_but_new_unlisted_rejected(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('model = "gpt-private-legacy"\nmodel_reasoning_effort = "medium"\n')
        saved = self.console.settings()['roles']['owner']
        self.assertEqual(saved['model'], 'gpt-private-legacy')
        plan = self.console.preview({'preset':'balanced', 'roles':{'owner':saved}})
        self.assertTrue(plan['can_save'])
        self.console.save({'preview_id':plan['preview_id']})
        self.assertEqual(self.console.settings()['roles']['owner']['model'], 'gpt-private-legacy')
        with self.assertRaisesRegex(ValueError, 'no longer in the model list'):
            self.console.preview({'preset':'balanced', 'roles':{'owner':{'model':'gpt-unlisted-new','effort':'medium'}}})

    def test_team_slots_allow_duplicate_duties_and_restore_safe_changes(self):
        config = self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('private_setting = "keep"\n[unrelated]\nvalue = 7\n')
        team = [
            {'slot':'team_slot_01','duty':'researcher','model':'gpt-6.1-sol','effort':'medium','title':'Sources'},
            {'slot':'team_slot_02','duty':'researcher','model':'gpt-6-luna','effort':'high','title':'Facts'},
        ]
        plan = self.console.preview({'preset':'focused','concurrency':2,'team':team})
        self.assertTrue(plan['can_save'],plan['conflicts'])
        self.console.save({'preview_id':plan['preview_id']})
        saved = self.console.settings()
        self.assertEqual(saved['team'],team)
        self.assertEqual(saved['concurrency'],2)
        self.assertEqual(dashboard.tomllib.loads(config.read_text())['private_setting'],'keep')
        self.assertEqual(dashboard.tomllib.loads(config.read_text())['agents']['team_slot_02']['config_file'],'./agents/team-slot-02.toml')
        first = self.target/dashboard.TEAM_SLOTS['team_slot_01']
        second = self.target/dashboard.TEAM_SLOTS['team_slot_02']
        self.assertEqual(dashboard.tomllib.loads(first.read_text())['name'],'team_slot_01')
        self.assertEqual(dashboard.tomllib.loads(second.read_text())['name'],'team_slot_02')
        self.assertTrue(dashboard.installer.unchanged_owned(dashboard.installer.load_manifest(self.target),dashboard.TEAM_SLOTS['team_slot_02'],second))
        config.write_text(config.read_text()+'\n[agents.team_slot_02.env]\nNOTE = "keep"\n')
        reduced = self.console.preview({'preset':'focused','concurrency':1,'team':team[:1]})
        self.assertTrue(reduced['can_save'],reduced['conflicts'])
        self.console.save({'preview_id':reduced['preview_id']})
        self.assertFalse(second.exists())
        self.assertNotIn('team_slot_02',dashboard.tomllib.loads(config.read_text())['agents'])
        self.console.restore({})
        self.assertTrue(second.exists())
        self.assertEqual(self.console.settings()['team'],team)
        self.assertEqual(dashboard.tomllib.loads(config.read_text())['agents']['team_slot_02']['env']['NOTE'],'keep')
        second.write_text(second.read_text()+'\n# personal edit\n')
        conflict = self.console.preview({'preset':'focused','concurrency':1,'team':team[:1]})
        self.assertIn(str(dashboard.TEAM_SLOTS['team_slot_02']),conflict['conflicts'])

    @unittest.skipUnless(dashboard.installer.SAFE_UNINSTALL_SUPPORTED, 'safe uninstall requires POSIX directory handles')
    def test_fifty_planned_slots_are_separate_from_concurrency_and_restore(self):
        duties = list(dashboard.installer.ROLE_FILES)
        team = [{'slot':f'team_slot_{index:02d}', 'duty':duties[(index-1)%len(duties)],
                 'model':'gpt-6.1-sol', 'effort':'medium', 'title':f'Part {index}'} for index in range(1,51)]
        plan = self.console.preview({'preset':'focused','concurrency':4,'team_count':50,'team':team})
        self.assertTrue(plan['can_save'], plan['conflicts'])
        self.console.save({'preview_id':plan['preview_id']})
        saved = self.console.settings()
        self.assertEqual((saved['team_count'],saved['concurrency'],len(saved['team'])),(50,4,50))
        self.assertEqual(saved['saved_preset'],'focused')
        last = self.target/dashboard.TEAM_SLOTS['team_slot_50']
        self.assertTrue(last.is_file())
        self.assertEqual(dashboard.tomllib.loads((self.target/'.codex/config.toml').read_text())['agents']['max_concurrent_threads_per_session'],4)
        smaller = self.console.preview({'preset':'focused','concurrency':2,'team_count':7,'team':team[:7]})
        self.assertTrue(smaller['can_save'], smaller['conflicts'])
        self.console.save({'preview_id':smaller['preview_id']})
        self.assertFalse(last.exists())
        self.assertEqual(self.console.settings()['team_count'],7)
        self.assertNotIn(dashboard.TEAM_SLOTS['team_slot_50'].as_posix(),
                         dashboard.installer.load_manifest(self.target)['files'])
        self.console.restore({})
        self.assertTrue(last.exists())
        self.assertEqual(self.console.settings()['team_count'],50)
        self.assertEqual(dashboard.installer.uninstall(self.target, False),0)
        self.assertFalse(last.exists())

    def test_fifty_slot_preflight_and_mid_save_rollback(self):
        team = [{'slot':f'team_slot_{index:02d}', 'duty':'researcher','model':'gpt-6.1-sol','effort':'medium','title':''}
                for index in range(1,51)]
        payload = {'preset':'focused','concurrency':4,'team_count':50,'team':team}
        for count in (0,51,True):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError,'Planned team size'):
                self.console.preview({**payload,'team_count':count})
        with self.assertRaisesRegex(ValueError,'one helper per selected slot'):
            self.console.preview({**payload,'team_count':49})
        outside = Path(self.tmp.name)/'outside.toml'
        leaf = self.target/dashboard.TEAM_SLOTS['team_slot_50']
        leaf.parent.mkdir(parents=True)
        leaf.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'Symlink destination refused'):
            self.console.preview(payload)
        self.assertFalse(outside.exists())
        leaf.unlink()
        plan = self.console.preview(payload)
        before = self.console.snapshot()
        original = dashboard.installer.install_text_file
        def fail_on_thirtieth(**kwargs):
            if kwargs.get('relative') == dashboard.TEAM_SLOTS['team_slot_30']:
                raise OSError('injected write failure')
            return original(**kwargs)
        with mock.patch.object(dashboard.installer,'install_text_file',side_effect=fail_on_thirtieth):
            with self.assertRaisesRegex(OSError,'injected write failure'):
                self.console.save({'preview_id':plan['preview_id']})
        self.assertEqual(self.console.snapshot(),before)
        self.assertFalse((self.target/dashboard.installer.MANIFEST_RELATIVE).exists())

    def test_new_slot_created_during_save_is_never_replaced_or_rolled_back(self):
        team = [{'slot':f'team_slot_{index:02d}', 'duty':'researcher','model':'gpt-6.1-sol','effort':'medium','title':''}
                for index in range(1,51)]
        plan = self.console.preview({'preset':'focused','concurrency':4,'team_count':50,'team':team})
        leaf = self.target/dashboard.TEAM_SLOTS['team_slot_25']
        original = dashboard.installer.install_text_file
        def create_racing_file(**kwargs):
            if kwargs.get('relative') == dashboard.TEAM_SLOTS['team_slot_25']:
                leaf.write_text('OWNER_NEW_FILE = "keep"\n')
            return original(**kwargs)
        with mock.patch.object(dashboard.installer,'install_text_file',side_effect=create_racing_file):
            with self.assertRaises((FileExistsError,dashboard.installer.InstallError)):
                self.console.save({'preview_id':plan['preview_id']})
        self.assertEqual(leaf.read_text(),'OWNER_NEW_FILE = "keep"\n')
        self.assertFalse((self.target/dashboard.TEAM_SLOTS['team_slot_24']).exists())
        self.assertFalse((self.target/dashboard.installer.MANIFEST_RELATIVE).exists())

    def test_existing_managed_file_changed_during_save_is_preserved(self):
        initial = self.console.preview({'preset':'focused'})
        self.console.save({'preview_id':initial['preview_id']})
        explorer = self.target/dashboard.installer.ROLE_FILES['explorer']
        manifest = self.target/dashboard.installer.MANIFEST_RELATIVE
        old_manifest = manifest.read_bytes()
        plan = self.console.preview({'preset':'quality'})
        original = dashboard.installer.install_text_file
        def edit_after_precheck(**kwargs):
            if kwargs.get('relative') == dashboard.installer.ROLE_FILES['explorer']:
                explorer.write_bytes(explorer.read_bytes()+b'\n# OWNER CONCURRENT EDIT\n')
            return original(**kwargs)
        with mock.patch.object(dashboard.installer,'install_text_file',side_effect=edit_after_precheck):
            with self.assertRaisesRegex(dashboard.installer.InstallError,'changed during installation'):
                self.console.save({'preview_id':plan['preview_id']})
        self.assertIn(b'OWNER CONCURRENT EDIT', explorer.read_bytes())
        self.assertEqual(manifest.read_bytes(), old_manifest)
        self.assertEqual(self.console.settings()['roles']['owner']['model'], 'gpt-6.1-sol')

    def test_existing_crlf_config_and_agent_keep_raw_snapshot_through_restore(self):
        first = self.console.preview({'preset':'focused'})
        self.console.save({'preview_id':first['preview_id']})
        config = self.target/dashboard.installer.CONFIG_RELATIVE
        role = dashboard.installer.ROLE_FILES['explorer']
        agent = self.target/role
        manifest_path = self.target/dashboard.installer.MANIFEST_RELATIVE
        config.write_bytes(config.read_bytes().replace(b'\n', b'\r\n'))
        agent.write_bytes(agent.read_bytes().replace(b'\n', b'\r\n'))
        manifest = dashboard.installer.load_manifest(self.target)
        manifest['files'][role.as_posix()]['sha256'] = dashboard.installer.sha256_path(agent)
        manifest_path.write_text(json.dumps(manifest))
        before = self.console.snapshot()
        change = self.console.preview({'preset':'quality'})
        self.assertTrue(change['can_save'], change['conflicts'])
        self.console.save({'preview_id':change['preview_id']})
        self.console.restore({})
        self.assertEqual(self.console.snapshot(), before)
        self.assertIn(b'\r\n', config.read_bytes())
        self.assertIn(b'\r\n', agent.read_bytes())

    def test_fifty_slot_restore_failure_rolls_back_and_can_retry(self):
        team = [{'slot':f'team_slot_{index:02d}', 'duty':'researcher','model':'gpt-6.1-sol','effort':'medium','title':''}
                for index in range(1,51)]
        full = self.console.preview({'preset':'focused','concurrency':4,'team_count':50,'team':team})
        self.console.save({'preview_id':full['preview_id']})
        smaller = self.console.preview({'preset':'focused','concurrency':4,'team_count':3,'team':team[:3]})
        self.console.save({'preview_id':smaller['preview_id']})
        before = self.console.snapshot()
        history = (self.target/dashboard.HISTORY).read_bytes()
        state = (self.target/dashboard.STATE).read_bytes()
        original = self.console.restore_bytes
        injected = False
        def fail_after_twenty_fifth(name, data, **kwargs):
            nonlocal injected
            original(name, data, **kwargs)
            if name == str(dashboard.TEAM_SLOTS['team_slot_25']) and not injected:
                injected = True
                raise OSError('injected restore failure')
        with mock.patch.object(self.console,'restore_bytes',side_effect=fail_after_twenty_fifth):
            with self.assertRaisesRegex(OSError,'injected restore failure'):
                self.console.restore({})
        self.assertEqual(self.console.snapshot(),before)
        self.assertEqual((self.target/dashboard.HISTORY).read_bytes(),history)
        self.assertEqual((self.target/dashboard.STATE).read_bytes(),state)
        self.console.restore({})
        self.assertEqual(self.console.settings()['team_count'],50)

    def test_manifest_dangling_symlink_inserted_during_save_is_not_replaced(self):
        plan = self.console.preview({'preset':'focused'})
        manifest = self.target/dashboard.installer.MANIFEST_RELATIVE
        outside = Path(self.tmp.name)/'outside.json'
        original = dashboard.installer.write_manifest
        def create_racing_symlink(**kwargs):
            manifest.symlink_to(outside)
            return original(**kwargs)
        with mock.patch.object(dashboard.installer,'write_manifest',side_effect=create_racing_symlink):
            with self.assertRaises((FileExistsError,dashboard.installer.InstallError)):
                self.console.save({'preview_id':plan['preview_id']})
        self.assertTrue(manifest.is_symlink())
        self.assertFalse(outside.exists())
        self.assertFalse((self.target/'.codex/agents/researcher.toml').exists())

    def test_fifty_slot_conflict_and_changed_preview_refuse_write(self):
        team = [{'slot':f'team_slot_{index:02d}', 'duty':'researcher','model':'gpt-6.1-sol','effort':'medium','title':''}
                for index in range(1,51)]
        plan = self.console.preview({'preset':'focused','concurrency':4,'team_count':50,'team':team})
        self.console.save({'preview_id':plan['preview_id']})
        last = self.target/dashboard.TEAM_SLOTS['team_slot_50']
        original = last.read_bytes()
        last.write_text(last.read_text()+'\n# owner edit\n')
        before = last.read_bytes()
        smaller = self.console.preview({'preset':'focused','concurrency':4,'team_count':3,'team':team[:3]})
        self.assertIn(str(dashboard.TEAM_SLOTS['team_slot_50']),smaller['conflicts'])
        with self.assertRaisesRegex(ValueError,'Managed file conflict'):
            self.console.save({'preview_id':smaller['preview_id']})
        self.assertEqual(last.read_bytes(),before)
        last.write_bytes(original)
        clean = self.console.preview({'preset':'focused','concurrency':4,'team_count':3,'team':team[:3]})
        self.assertTrue(clean['can_save'])
        last.write_bytes(original+b'\n# newer owner edit\n')
        with self.assertRaisesRegex(ValueError,'since preview'):
            self.console.save({'preview_id':clean['preview_id']})

    def test_team_rejects_invalid_slot_or_effort(self):
        team = [{'slot':'team_slot_01','duty':'researcher','model':'gpt-6-luna','effort':'ultra','title':''}]
        with self.assertRaisesRegex(ValueError, 'not supported'):
            self.console.preview({'preset':'focused','concurrency':1,'team':team})
        team[0]['effort']='high'
        team[0]['slot']='other'
        with self.assertRaisesRegex(ValueError, 'Invalid team slot'):
            self.console.preview({'preset':'focused','concurrency':1,'team':team})

    def test_backup_symlink_ancestor_refused_before_save(self):
        config=self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('secret = "keep"\n')
        backup_root=self.target/dashboard.installer.BACKUP_RELATIVE
        backup_root.mkdir(parents=True)
        escape=Path(self.tmp.name)/'escape'
        escape.mkdir()
        with mock.patch.object(dashboard.installer,'timestamp_for_path',return_value='20260101T000000Z'):
            (backup_root/'20260101T000000Z').symlink_to(escape, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Symlink backup path refused'):
                self.console.preview({'preset':'focused'})
        self.assertEqual(config.read_text(),'secret = "keep"\n')
        self.assertEqual(list(escape.iterdir()),[])

    def test_dangling_backup_leaf_symlink_refused_before_save(self):
        config=self.target/'.codex/config.toml'
        config.parent.mkdir()
        config.write_text('secret = "keep"\n')
        backup_root=self.target/dashboard.installer.BACKUP_RELATIVE
        leaf=backup_root/'20260101T000000Z'/'.codex/config.toml'
        leaf.parent.mkdir(parents=True)
        outside=Path(self.tmp.name)/'stolen.toml'
        leaf.symlink_to(outside)
        before=config.read_bytes()
        with mock.patch.object(dashboard.installer,'timestamp_for_path',return_value='20260101T000000Z'):
            with self.assertRaisesRegex(ValueError, 'Symlink backup path refused'):
                self.console.preview({'preset':'focused'})
        self.assertEqual(config.read_bytes(),before)
        self.assertFalse(outside.exists())

    def test_model_effort_pair_and_existing_legacy_pair(self):
        import model_catalog
        with mock.patch.object(model_catalog, 'discover_cli', return_value=None):
            self.console._catalog = model_catalog.catalog()
        with self.assertRaisesRegex(ValueError, 'reasoning effort is not supported'):
            self.console.preview({'preset':'balanced', 'roles':{'explorer':{'model':'gpt-6-luna','effort':'ultra'}}})
        self.assertTrue(self.console.preview({'preset':'balanced', 'roles':{'owner':{'model':'gpt-6.1-sol','effort':'ultra'}}})['can_save'])
        self.assertTrue(self.console.preview({'preset':'balanced', 'roles':{'owner':{'model':'gpt-6.1-sol','effort':'max'}}})['can_save'])
        self.assertTrue(self.console.preview({'preset':'balanced', 'roles':{'owner':{'model':'gpt-6-astra','effort':'ultra'}}})['can_save'])
        role = self.target/'.codex/agents/explorer.toml'
        role.parent.mkdir(parents=True)
        role.write_text('model="gpt-6-luna"\nmodel_reasoning_effort="ultra"\n')
        self.console.preview({'preset':'balanced', 'roles':{'explorer':{'model':'gpt-6-luna','effort':'ultra'}}})
        with self.assertRaisesRegex(ValueError, 'reasoning effort is not supported'):
            self.console.preview({'preset':'balanced', 'roles':{'explorer':{'model':'gpt-6-luna','effort':'minimal'}}})

    def test_every_builtin_preset_uses_catalog_supported_efforts(self):
        import model_catalog
        self.console._catalog = model_catalog.catalog()
        for preset in dashboard.installer.PRESETS:
            with self.subTest(preset=preset):
                self.assertTrue(self.console.preview({'preset':preset})['can_save'])
        self.assertTrue(all('astra' not in model for model, _ in dashboard.installer.PRESETS['focused'].values()))

    def test_http_security_api_and_assets(self):
        server = dashboard.Server(('127.0.0.1',0), self.console)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        try:
            conn.request('GET','/api/settings')
            result = conn.getresponse()
            self.assertEqual(result.status, 200)
            token=json.loads(result.read())['csrf_token']
            conn.request('GET','/api/models')
            result=conn.getresponse();self.assertEqual(result.status,200)
            models=json.loads(result.read())
            self.assertIn('gpt-6.1-sol',{item['id'] for item in models['models']})
            conn.request('GET','/api/usage')
            result=conn.getresponse()
            self.assertEqual(json.loads(result.read())['status'],'unavailable')
            conn.request('POST','/api/save', '{}', {'Content-Type':'application/json'})
            result=conn.getresponse(); self.assertEqual(result.status,403);result.read()
            headers={'Content-Type':'application/json','Origin':server.origin,'X-CSRF-Token':token}
            conn.request('POST','/api/models/refresh','{}',headers)
            result=conn.getresponse();self.assertEqual(result.status,200);result.read()
            conn.request('POST','/api/preview', '{}', headers)
            result=conn.getresponse();self.assertEqual(result.status,200);plan=json.loads(result.read())
            conn.request('POST','/api/save', json.dumps({'preview_id':plan['preview_id']}),headers)
            result=conn.getresponse();self.assertEqual(result.status,200);result.read()
            conn.request('POST','/api/restore', '{}', headers)
            result=conn.getresponse();self.assertEqual(result.status,200);result.read()
            conn.request('GET','/api/usage?date_from=bad')
            result=conn.getresponse();self.assertEqual(result.status,400);result.read()
            conn.request('GET','/../../config.toml')
            result=conn.getresponse();self.assertEqual(result.status,404);result.read()
            conn.request('GET','/',headers={'Host':'evil.example'})
            result=conn.getresponse();self.assertEqual(result.status,403);result.read()
            for route in ('/','/style.css','/app.js'):
                conn.request('GET',route)
                result=conn.getresponse();self.assertEqual(result.status,200);self.assertTrue(result.read())
        finally:
            conn.close();server.shutdown();server.server_close();worker.join()

    def test_idle_browser_connection_does_not_block_other_requests(self):
        server = dashboard.Server(('127.0.0.1', 0), self.console)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        idle = socket.create_connection(('127.0.0.1', server.server_port), timeout=3)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            conn.request('GET', '/')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b'Codex', response.read())
        finally:
            idle.close();conn.close();server.shutdown();server.server_close();worker.join()

if __name__ == '__main__':
    unittest.main()
