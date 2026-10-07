"""Adversarial local work protocol tests; never execute provider model calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from ustam.protocol import WorkProtocol, ProtocolError, digest, snapshot


def provider_fixture_root(source, provider):
    """Use only this checkout's canonical immutable provider source prefix."""
    repo = source / 'engine-sources' / provider
    for name in ('scripts/install.py', '.' + provider + '/tools/work_protocol.py', '.' + provider + '/tools/work_protocol_core.py'):
        if not (repo / name).is_file():
            raise AssertionError('Missing ' + provider + ' protocol fixture: ' + str(repo / name))
    return repo


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.git('init')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.project / 'result.txt').write_text('old')
        (self.project / 'other.txt').write_text('user baseline')
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')
        self.protocol = WorkProtocol(self.root / 'state' / 'works')
        self.c = {'scope': 'Change result', 'criteria': ['Result contains success'], 'files': ['result.txt'], 'commands': []}
        self.pack = {'author': 'acceptance_test_author', 'checks': [{'criterion': 0, 'kind': 'contains', 'path': 'result.txt', 'expected': 'success'}], 'good': {'result.txt': 'success'}, 'bad': [{'result.txt': 'old'}]}
    def tearDown(self):
        self.temp.cleanup()
    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.project, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    def op(self, action, w=None, trusted=False, **payload):
        if w:
            payload.update(id=w['id'], version=w['version'])
        return self.protocol.mutate(action, payload, uuid.uuid4().hex, trusted=trusted)
    def create(self, provider='codex', c=None):
        return self.op('create', provider=provider, project_id='registered', path=str(self.project), contract=c or self.c)
    def prepare(self, provider='codex'):
        w = self.create(provider)
        w = self.op('test_pack', w, pack=self.pack)
        return self.op('prepare', w)
    def checked(self):
        w = self.prepare()
        (Path(w['workspace']) / 'result.txt').write_text('success')
        w = self.op('freeze', w)
        return self.op('check', w, role='verifier')
    def test_routine_contract_no_second_approval_material_revision_invalidates(self):
        w = self.create()
        self.assertFalse(w['approval_required'])
        self.assertEqual(w['approval_basis'], 'standing_policy')
        same = self.op('revise', w, contract=self.c)
        self.assertEqual(same['version'], 1)
        c = dict(self.c, scope='Materially different goal')
        revised = self.op('revise', same, contract=c)
        self.assertIsNone(revised['approved_hash'])
        self.assertEqual(revised['version'], 2)
        with self.assertRaisesRegex(ProtocolError, 'trusted'):
            self.op('approve', revised, contract_hash=revised['contract_hash'])
        approved = self.op('approve', revised, trusted=True, contract_hash=revised['contract_hash'])
        self.assertEqual(approved['approval_basis'], 'trusted_ustam_control')
    def test_revise_conflict_cross_provider_is_atomic_and_normalized(self):
        self.create('codex')
        second = self.create('claude', dict(self.c, files=['other.txt']))
        before = self.protocol.load()['works'][second['id']]
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('revise', second, contract=dict(self.c, files=['./result.txt']))
        self.assertEqual(self.protocol.load()['works'][second['id']], before)

    def test_resume_conflict_cross_provider_preserves_cancelled_candidate(self):
        first = self.checked()
        first = self.op('cancel', first)
        self.create('opencode')
        before = self.protocol.load()['works'][first['id']]
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('resume', first)
        self.assertEqual(self.protocol.load()['works'][first['id']], before)

    def test_cancelled_revision_cannot_reacquire_live_ownership(self):
        first = self.op('cancel', self.create())
        self.create('claude')
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('revise', first, contract=dict(self.c, scope='Changed goal'))
        self.assertEqual(self.protocol.load()['works'][first['id']]['status'], 'cancelled')

    def test_ownership_canonical_project_alias_and_duplicate_paths(self):
        self.create()
        alias = self.root / 'alias'
        alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('create', provider='claude', project_id='other-registry-id', path=str(alias), contract=dict(self.c, files=['./result.txt']))
        with self.assertRaisesRegex(ProtocolError, 'normalized'):
            self.create(c=dict(self.c, files=['result.txt', './result.txt']))

    def test_filesystem_case_alias_existing_and_nonexistent_ownership(self):
        insensitive = (self.project / 'RESULT.TXT').exists()
        first = self.create()
        if insensitive:
            with self.assertRaisesRegex(ProtocolError, 'owns'):
                self.create('claude', dict(self.c, files=['RESULT.TXT']))
        else:
            other = self.create('claude', dict(self.c, files=['RESULT.TXT']))
            self.op('cancel', other)
        self.op('cancel', first)
        first = self.create(c=dict(self.c, files=['future/result.txt']))
        if insensitive:
            with self.assertRaisesRegex(ProtocolError, 'owns'):
                self.create('opencode', dict(self.c, files=['FUTURE/RESULT.TXT']))
        else:
            self.create('opencode', dict(self.c, files=['FUTURE/RESULT.TXT']))

    def test_filesystem_project_case_alias_cannot_bypass_ownership(self):
        alias = self.project.with_name(self.project.name.upper())
        if not alias.exists():
            self.skipTest('Case-sensitive fixture volume')
        self.create()
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('create', provider='claude', project_id='other-id', path=str(alias), contract=self.c)

    def test_filesystem_unicode_existing_and_future_file_alias_ownership(self):
        composed = self.project / 'café.txt'
        decomposed = self.project / 'cafe\u0301.txt'
        composed.write_text('capability fixture')
        aliased = decomposed.exists() and composed.samefile(decomposed)
        if not aliased:
            composed.unlink()
            self.skipTest('Fixture filesystem distinguishes Unicode forms')
        first = self.create(c=dict(self.c, files=[composed.name]))
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.create('claude', dict(self.c, files=[decomposed.name]))
        self.op('cancel', first)
        composed.unlink()
        first = self.create(c=dict(self.c, files=['café-future/' + composed.name]))
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.create('opencode', dict(self.c, files=['cafe\u0301-future/' + decomposed.name]))
        with self.assertRaisesRegex(ProtocolError, 'normalized'):
            self.create('claude', dict(self.c, files=['other/' + composed.name, 'other/' + decomposed.name]))

    def test_filesystem_unicode_project_alias_cannot_bypass_ownership(self):
        composed = self.root / 'café-project'
        decomposed = self.root / 'cafe\u0301-project'
        composed.mkdir()
        if not decomposed.exists() or not composed.samefile(decomposed):
            self.skipTest('Fixture filesystem distinguishes Unicode forms')
        self.op('create', provider='codex', project_id='unicode-a', path=str(composed), contract=self.c)
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('create', provider='claude', project_id='unicode-b', path=str(decomposed), contract=self.c)

    def test_unicode_revision_and_resume_conflicts_leave_work_unchanged(self):
        capability = self.project / 'café-capability'
        capability.write_text('capability fixture')
        alias = self.project / 'cafe\u0301-capability'
        aliased = alias.exists() and capability.samefile(alias)
        capability.unlink()
        if not aliased:
            self.skipTest('Fixture filesystem distinguishes Unicode forms')
        first = self.create(c=dict(self.c, files=['café-future/café.txt']))
        second = self.create('claude', dict(self.c, files=['other.txt']))
        before = self.protocol.load()['works'][second['id']]
        overlapping = dict(self.c, files=['cafe\u0301-future/cafe\u0301.txt'])
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('revise', second, contract=overlapping)
        self.assertEqual(self.protocol.load()['works'][second['id']], before)
        first = self.op('cancel', first)
        self.op('revise', second, contract=overlapping)
        before = self.protocol.load()['works'][first['id']]
        with self.assertRaisesRegex(ProtocolError, 'owns'):
            self.op('resume', first)
        self.assertEqual(self.protocol.load()['works'][first['id']], before)

    def test_case_sensitive_future_path_keys_preserve_distinct_cases(self):
        with patch.object(WorkProtocol, '_case_sensitive', return_value=True):
            first = self.create(c=dict(self.c, files=['future/result.txt']))
            second = self.create('claude', dict(self.c, files=['future/RESULT.TXT']))
        self.assertNotEqual(first['id'], second['id'])

    def test_raw_linux_future_unicode_names_remain_distinct(self):
        with patch('ustam.protocol.sys.platform', 'linux'):
            first = self.create(c=dict(self.c, files=['future/café.txt']))
            second = self.create('claude', dict(self.c, files=['future/cafe\u0301.txt']))
        self.assertNotEqual(first['id'], second['id'])

    def test_apply_rollback_preserves_intervening_bytes_mode_inode_and_symlink(self):
        for change in ('bytes', 'mode', 'inode', 'symlink'):
            with self.subTest(change=change):
                w = self.op('integration_check', self.checked())
                dest = self.project / 'result.txt'
                original = self.protocol._copy_changes
                outside = self.root / 'outside.txt'
                outside.write_text('outside untouched')
                def fail_after_copy(work, root, changed):
                    original(work, root, changed)
                    if change == 'bytes':
                        dest.write_text('concurrent user edit')
                    elif change == 'mode':
                        dest.chmod(0o700)
                    elif change == 'inode':
                        replacement = self.project / 'replacement'
                        replacement.write_text('success')
                        replacement.chmod(dest.stat().st_mode & 0o777)
                        os.replace(replacement, dest)
                    else:
                        dest.unlink()
                        dest.symlink_to(outside)
                    raise OSError('injected write failure')
                with patch.object(self.protocol, '_copy_changes', side_effect=fail_after_copy):
                    with self.assertRaises(OSError):
                        self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
                retained = self.protocol.load()['works'][w['id']]
                self.assertEqual(retained['status'], 'needs_attention')
                self.assertIn('Intervening changes', retained['message'])
                recovery = next(self.protocol.directory.glob('apply-recovery-*'))
                self.assertEqual((recovery / '0').read_bytes(), b'old')
                if os.name != 'nt':
                    self.assertEqual(recovery.stat().st_mode & 0o777, 0o700)
                    self.assertEqual((recovery / '0').stat().st_mode & 0o777, 0o600)
                self.assertEqual(outside.read_text(), 'outside untouched')
                if change == 'bytes': self.assertEqual(dest.read_text(), 'concurrent user edit')
                elif change == 'mode': self.assertEqual(dest.stat().st_mode & 0o777, 0o700)
                elif change == 'inode': self.assertEqual(dest.read_text(), 'success')
                else: self.assertTrue(dest.is_symlink())
                self.op('cancel', retained)
                dest.unlink()
                dest.write_text('old')
                for folder in self.protocol.directory.glob('apply-recovery-*'):
                    import shutil
                    shutil.rmtree(folder)

    def test_apply_rollback_restores_unchanged_writes(self):
        w = self.op('integration_check', self.checked())
        original = self.protocol._copy_changes
        def fail_after_copy(work, root, changed):
            original(work, root, changed)
            raise OSError('injected failure')
        with patch.object(self.protocol, '_copy_changes', side_effect=fail_after_copy):
            with self.assertRaises(OSError):
                self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
        self.assertEqual((self.project / 'result.txt').read_text(), 'old')
        retained = self.protocol.load()['works'][w['id']]
        self.assertEqual(retained['status'], 'needs_attention')
        self.assertIn('rolled back', retained['message'])

    def test_apply_refuses_destination_changes_before_first_write(self):
        for change in ('bytes', 'mode', 'inode', 'deleted'):
            with self.subTest(change=change):
                w = self.op('integration_check', self.checked())
                dest = self.project / 'result.txt'
                original = self.protocol._copy_changes
                def changed_before_copy(work, root, changed):
                    if change == 'bytes': dest.write_text('concurrent user edit before apply')
                    elif change == 'mode': dest.chmod(0o700)
                    elif change == 'inode':
                        replacement = self.project / 'replacement'
                        replacement.write_text('old')
                        replacement.chmod(dest.stat().st_mode & 0o777)
                        os.replace(replacement, dest)
                    else: dest.unlink()
                    return original(work, root, changed)
                with patch.object(self.protocol, '_copy_changes', side_effect=changed_before_copy):
                    with self.assertRaisesRegex(ProtocolError, 'Main changed'):
                        self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
                retained = self.protocol.load()['works'][w['id']]
                self.assertEqual(retained['status'], 'needs_attention')
                if change == 'bytes': self.assertEqual(dest.read_text(), 'concurrent user edit before apply')
                elif change == 'mode': self.assertEqual(dest.stat().st_mode & 0o777, 0o700)
                elif change == 'inode': self.assertEqual(dest.read_text(), 'old')
                else: self.assertFalse(dest.exists())
                self.op('cancel', retained)
                dest.unlink(missing_ok=True)
                dest.write_text('old')

    def test_apply_stops_if_candidate_changes_while_copy_is_prepared(self):
        w = self.op('integration_check', self.checked())
        chmod = os.chmod
        def mutate_candidate(path, mode):
            chmod(path, mode)
            if Path(path).name.startswith('.ustam-apply-'):
                (Path(w['workspace']) / 'result.txt').write_text('changed candidate')
        with patch('ustam.protocol.os.chmod', side_effect=mutate_candidate):
            with self.assertRaisesRegex(ProtocolError, 'Candidate file changed'):
                self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
        self.assertEqual((self.project / 'result.txt').read_text(), 'old')
        self.assertEqual(self.protocol.load()['works'][w['id']]['status'], 'needs_attention')

    def test_apply_new_file_collision_and_clean_deletion_rollback(self):
        w = self.create(c=dict(self.c, files=['result.txt', 'other.txt', 'new.txt']))
        self.op('test_pack', w, pack=self.pack)
        w = self.op('prepare', w)
        workspace = Path(w['workspace'])
        (workspace / 'result.txt').write_text('success')
        (workspace / 'other.txt').unlink()
        (workspace / 'new.txt').write_text('candidate new')
        w = self.op('freeze', w)
        w = self.op('check', w, role='verifier')
        w = self.op('integration_check', w)
        original = self.protocol._copy_changes
        def collision(work, root, changed):
            (self.project / 'new.txt').write_text('user created before apply')
            original(work, root, changed)
        with patch.object(self.protocol, '_copy_changes', side_effect=collision):
            with self.assertRaisesRegex(ProtocolError, 'Main changed'):
                self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
        self.assertEqual((self.project / 'new.txt').read_text(), 'user created before apply')
        self.assertEqual((self.project / 'other.txt').read_text(), 'user baseline')
        self.assertEqual((self.project / 'result.txt').read_text(), 'old')

    def test_apply_rollback_deleted_file_recreated_by_user_is_preserved(self):
        w = self.create(c=dict(self.c, files=['result.txt', 'other.txt', 'new.txt']))
        self.op('test_pack', w, pack=self.pack)
        w = self.op('prepare', w)
        workspace = Path(w['workspace'])
        (workspace / 'result.txt').write_text('success')
        (workspace / 'other.txt').unlink()
        (workspace / 'new.txt').write_text('candidate new')
        w = self.op('freeze', w)
        w = self.op('check', w, role='verifier')
        w = self.op('integration_check', w)
        original = self.protocol._copy_changes
        def fail_after_copy(work, root, changed):
            original(work, root, changed)
            (self.project / 'other.txt').write_text('user recreated')
            (self.project / 'new.txt').write_text('user new edit')
            raise OSError('injected failure')
        with patch.object(self.protocol, '_copy_changes', side_effect=fail_after_copy):
            with self.assertRaises(OSError):
                self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
        self.assertEqual((self.project / 'other.txt').read_text(), 'user recreated')
        self.assertEqual((self.project / 'new.txt').read_text(), 'user new edit')
        self.assertEqual((self.project / 'result.txt').read_text(), 'old')

    def test_command_authorization_cannot_be_forged(self):
        w = self.create(c=dict(self.c, commands=[[sys.executable, '-m', 'unittest']]))
        self.assertIsNone(w['approved_hash'])
        self.op('test_pack', w, pack=self.pack)
        with self.assertRaisesRegex(ProtocolError, 'approval'):
            self.op('prepare', w)
    def test_quality_good_pass_bad_fail_and_tests_before_implementation(self):
        w = self.create()
        with self.assertRaisesRegex(ProtocolError, 'pack required'):
            self.op('prepare', w)
        wrong = dict(self.pack, bad=[{'result.txt': 'success'}])
        with self.assertRaisesRegex(ProtocolError, 'quality failed'):
            self.op('test_pack', w, pack=wrong)
        unsupported = dict(self.pack, checks=[dict(self.pack['checks'][0], kind='browser')])
        with self.assertRaisesRegex(ProtocolError, 'Unsupported'):
            self.op('test_pack', w, pack=unsupported)
        self.op('test_pack', w, pack=self.pack)
        w = self.op('prepare', w)
        with self.assertRaisesRegex(ProtocolError, 'precede'):
            self.op('test_pack', w, pack=self.pack)
    def test_dirty_git_context_preserved_check_apply_and_no_auto_commit(self):
        (self.project / 'other.txt').write_text('dirty user change')
        self.git('add', 'other.txt')
        (self.project / 'untracked.txt').write_text('user untracked')
        baseline = snapshot(self.project)
        w = self.checked()
        self.assertEqual(snapshot(self.project), baseline)
        self.assertEqual((Path(w['workspace']) / 'other.txt').read_text(), 'dirty user change')
        self.assertEqual((Path(w['workspace']) / 'untracked.txt').read_text(), 'user untracked')
        self.assertEqual(w['result']['provenance'], 'controller_observed')
        w = self.op('integration_check', w)
        self.assertEqual(w['status'], 'ready_to_apply')
        with self.assertRaisesRegex(ProtocolError, 'trusted'):
            self.op('apply', w, approve=True, integration_hash=w['integration_hash'])
        w = self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
        self.assertEqual(w['status'], 'applied')
        self.assertEqual((self.project / 'result.txt').read_text(), 'success')
        self.assertEqual((self.project / 'other.txt').read_text(), 'dirty user change')
        self.assertEqual(snapshot(self.project)['head'], baseline['head'])
        self.assertEqual(snapshot(self.project)['index'], baseline['index'])
    def test_candidate_change_invalidates_result_and_helper_report_not_verified(self):
        w = self.checked()
        reported = self.op('reported_check', w)
        self.assertIsNone(reported['result'])
        self.assertEqual(reported['receipts'][-1]['provenance'], 'helper_reported')
        w = self.op('check', w, role='verifier')
        (Path(w['workspace']) / 'result.txt').write_text('moved')
        visible = self.protocol.list()[0]
        self.assertEqual(visible['status'], 'candidate_changed')
        self.assertIsNone(visible['result'])
        with self.assertRaisesRegex(ProtocolError, 'Candidate changed'):
            self.op('integration_check', w)
    def test_main_advance_requires_revalidate_and_conflicting_owned_change_stops(self):
        w = self.checked()
        w = self.op('integration_check', w)
        (self.project / 'other.txt').write_text('main advanced')
        self.git('add', 'other.txt')
        self.git('commit', '-m', 'advance')
        with self.assertRaisesRegex(ProtocolError, 'stale'):
            self.op('apply', w, trusted=True, approve=True, integration_hash=w['integration_hash'])
        w = self.op('integration_check', w)
        self.assertEqual(w['status'], 'ready_to_apply')
        (self.project / 'result.txt').write_text('conflicting user edit')
        with self.assertRaisesRegex(ProtocolError, 'conflict'):
            self.op('integration_check', w)
    def test_question_dedup_and_scoped_stale_answers(self):
        w = self.create()
        w = self.op('question', w, question=' Choose format? ', options=['A', 'B'])
        w = self.op('question', w, question='choose   format?', options=['A', 'B'])
        self.assertEqual(len(w['questions']), 1)
        with self.assertRaisesRegex(ProtocolError, 'awaits answer'):
            self.op('prepare', w)
        with self.assertRaisesRegex(ProtocolError, 'trusted'):
            self.op('answer', w, question_id=w['questions'][0]['id'], answer='A')
        answered = self.op('answer', w, trusted=True, question_id=w['questions'][0]['id'], answer='A')
        self.assertEqual(answered['questions'][0]['answer'], 'A')
        self.assertEqual(answered['status'], 'contract')
        c = dict(self.c, scope='New scope')
        revised = self.op('revise', answered, contract=c)
        with self.assertRaisesRegex(ProtocolError, 'stale'):
            self.op('answer', revised, trusted=True, question_id=w['questions'][0]['id'], answer='B')
    def test_idempotency_restart_cancel_resume_preserve_candidate(self):
        w = self.checked()
        payload = {'id': w['id'], 'version': w['version']}
        operation = uuid.uuid4().hex
        first = self.protocol.mutate('cancel', payload, operation)
        self.protocol = WorkProtocol(self.protocol.directory)
        again = self.protocol.mutate('cancel', payload, operation)
        self.assertEqual(first, again)
        self.assertEqual(first['candidate']['hash'], w['candidate']['hash'])
        resumed = self.op('resume', first)
        self.assertEqual(resumed['candidate']['hash'], w['candidate']['hash'])
        with self.assertRaisesRegex(ProtocolError, 'reused'):
            self.protocol.mutate('resume', payload, operation)
        data = self.protocol.load()
        data['operations']['interrupted'] = {'hash': digest({'action': 'check', 'payload': payload}), 'status': 'pending', 'work_id': w['id']}
        self.protocol.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ProtocolError, 'Interrupted'):
            self.protocol.mutate('check', payload, 'interrupted')
    def test_unknown_schema_readonly_and_corrupt_metadata_not_rewritten(self):
        self.protocol.path.write_text('{"schema_version":999,"works":{},"operations":{}}')
        before = self.protocol.path.read_bytes()
        with self.assertRaisesRegex(ProtocolError, 'read only'):
            self.create()
        self.assertEqual(self.protocol.path.read_bytes(), before)
    def test_non_git_never_initializes_repository(self):
        plain = self.root / 'plain'
        plain.mkdir()
        w = self.op('create', provider='codex', project_id='plain', path=str(plain), contract=self.c)
        self.op('test_pack', w, pack=self.pack)
        w = self.op('prepare', w)
        self.assertEqual(w['status'], 'unsupported')
        self.assertFalse((plain / '.git').exists())
    def test_out_of_scope_file_and_symlink_rejected(self):
        w = self.prepare()
        (Path(w['workspace']) / 'other.txt').write_text('unauthorized')
        with self.assertRaisesRegex(ProtocolError, 'outside exclusive ownership'):
            self.op('freeze', w)
        with self.assertRaisesRegex(ProtocolError, 'Unsafe'):
            self.create(c=dict(self.c, files=['../escape']))
    def test_three_provider_public_native_cli_subprocess_and_no_model_call(self):
        source = Path(__file__).resolve().parents[1]
        state = self.root / 'state'
        (state / 'hub.json').write_text(json.dumps({'projects': [{'id': 'registered', 'path': str(self.project.resolve())}]}))
        for provider in ('codex', 'claude', 'opencode'):
            with self.subTest(provider=provider):
                tool = self.root / provider / ('.' + provider) / 'tools'
                tool.mkdir(parents=True)
                fixture_tools = provider_fixture_root(source, provider) / ('.' + provider) / 'tools'
                (tool / 'work_protocol.py').write_bytes((fixture_tools / 'work_protocol.py').read_bytes())
                (tool / 'work_protocol_core.py').write_bytes((fixture_tools / 'work_protocol_core.py').read_bytes())
                c = dict(self.c, files=[provider + '.txt'])
                payload = json.dumps({'contract': c})
                command = [sys.executable, str(tool / 'work_protocol.py'), 'create', '--state-dir', str(state), '--project', str(self.project), '--operation-id', uuid.uuid4().hex]
                proc = subprocess.run(command, input=payload, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                w = json.loads(proc.stdout)['work']
                self.assertEqual(w['provider'], provider)
                self.assertIsNone(w['workspace'])
                self.assertEqual(w['source'], 'local_protocol')
                forbidden = subprocess.run([sys.executable, str(tool / 'work_protocol.py'), 'approve', '--project', str(self.project)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertNotEqual(forbidden.returncode, 0)

    def test_combined_tree_runs_required_commands_with_exact_permission(self):
        c = dict(self.c, commands=[[sys.executable, '-c', 'from pathlib import Path; assert Path("other.txt").read_text() == "user baseline"']])
        w = self.create(c=c)
        w = self.op('approve', w, trusted=True, contract_hash=w['contract_hash'])
        self.op('test_pack', w, pack=self.pack)
        w = self.op('prepare', w)
        (Path(w['workspace']) / 'result.txt').write_text('success')
        w = self.op('freeze', w)
        with self.assertRaisesRegex(ProtocolError, 'Every approved'):
            self.op('check', w, role='verifier', commands=[])
        w = self.op('check', w, role='verifier')
        self.assertEqual(len(w['receipts'][-1]['commands']), 1)
        self.assertIn('executable_hash', w['receipts'][-1]['commands'][0])
        (self.project / 'other.txt').write_text('changed main context')
        w = self.op('integration_check', w)
        self.assertEqual(w['status'], 'needs_attention')
        self.assertEqual(w['integration']['commands'][0]['exit_code'], 1)
    def test_affected_tasks_share_one_question_and_answer_without_execution(self):
        first = self.create()
        c = dict(self.c, files=['second.txt'])
        second = self.create(c=c)
        first = self.op('question', first, question='Choose shared format', options=['A','B'], affected_tasks=[first['id'],second['id']])
        second = self.op('question', second, question='Choose shared format', options=['A','B'], affected_tasks=[second['id'],first['id']])
        self.assertEqual(first['questions'][0]['id'], second['questions'][0]['id'])
        with self.assertRaisesRegex(ProtocolError, 'awaits answer'):
            self.op('prepare', second)
        self.op('answer', first, trusted=True, question_id=first['questions'][0]['id'], answer='A')
        records = self.protocol.list()
        self.assertTrue(all(w['questions'][0]['answer']=='A' for w in records))
        self.assertTrue(all(w['workspace'] is None for w in records))
    def test_mode_change_invalidates_candidate_and_interrupted_prepare_keeps_workspace(self):
        w = self.checked()
        path = Path(w['workspace']) / 'result.txt'
        path.chmod(0o755)
        with self.assertRaisesRegex(ProtocolError, 'Candidate changed'):
            self.op('check', w, role='verifier')
    def test_actual_three_source_installers_install_public_local_tool(self):
        canonical = Path(__file__).resolve().parents[1]
        for provider in ('codex','claude','opencode'):
            repo = provider_fixture_root(canonical, provider)
            target = self.root / ('install with spaces-'+provider)
            target.mkdir()
            command = [sys.executable, str(repo/'scripts/install.py')]
            command += ['--target',str(target),'--action','install','--profile','balanced'] if provider=='opencode' else [str(target)]
            environment = None
            if provider == 'claude':
                from ustam_claude_cli_fixture import create_version_only_cli
                metadata_cli = create_version_only_cli(self.root / 'source-cli-fixture')
                environment = dict(os.environ, PATH=str(metadata_cli.parent) + os.pathsep + os.environ.get('PATH', ''))
            process = subprocess.run(command, cwd=repo, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, provider+process.stdout+process.stderr)
            tools = target / ('.'+provider) / 'tools'
            self.assertTrue((tools/'work_protocol.py').is_file())
            self.assertTrue((tools/'work_protocol_core.py').is_file())
            self.assertTrue((target / ('.'+provider) / 'agents' / ('acceptance-test-author.toml' if provider=='codex' else 'acceptance-test-author.md')).is_file())
            self.assertFalse((target/'.git').exists())
            state = self.root / ('registry-'+provider)
            state.mkdir()
            (state/'hub.json').write_text(json.dumps({'projects':[{'id':provider,'path':str(target.resolve())}]}))
            proc = subprocess.run([sys.executable,str(tools/'work_protocol.py'),'list','--state-dir',str(state),'--project',str(target)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(proc.returncode, 0, proc.stdout+proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['works'],[])
            if os.name != 'nt':
                bridge=subprocess.run(['sh',str(tools/'work_protocol'),'list','--state-dir',str(state)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                self.assertEqual(bridge.returncode,0,bridge.stdout+bridge.stderr)
                self.assertEqual(json.loads(bridge.stdout)['works'],[])
                redirected=subprocess.run(['sh',str(tools/'work_protocol'),'list','--state-dir',str(state),'--project',str(self.project)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                self.assertNotEqual(redirected.returncode,0)
                self.assertIn('Repeated control option',redirected.stderr)

    def test_public_worker_entrypoint_uses_registered_default_state_no_provider_cli(self):
        home = self.root / 'native-home'
        if sys.platform == 'darwin':
            state = home / 'Library/Application Support/Ustam'
        elif os.name == 'nt':
            state = home / 'AppData/Local/Ustam'
        else:
            state = home / '.config/ustam'
        state.mkdir(parents=True)
        (state / 'hub.json').write_text(json.dumps({'projects': [{'id': 'registered', 'path': str(self.project.resolve())}]}))
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home/'AppData/Local'))
        env.pop('XDG_CONFIG_HOME', None)
        marker = home / 'paid-called'
        fakebins = home / 'bin'
        fakebins.mkdir()
        for provider in ('codex','claude','opencode'):
            cli = fakebins / provider
            cli.write_text('#!/bin/sh\ntouch '+str(marker)+'\nexit 99\n')
            cli.chmod(0o700)
        env['PATH'] = str(fakebins)+os.pathsep+env.get('PATH','')
        for provider in ('codex','claude','opencode'):
            c = dict(self.c, files=[provider+'.txt'])
            proc = subprocess.run([sys.executable,'-m','ustam','--work-protocol',provider,'create','--project',str(self.project),'--operation-id',uuid.uuid4().hex],
                                  input=json.dumps({'contract':c}), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, cwd=Path(__file__).resolve().parents[1], timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stdout+proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['work']['provider'],provider)
        self.assertFalse(marker.exists())
    def test_interrupted_prepare_retains_workspace_intent_and_prevents_unsafe_resume(self):
        w = self.create()
        self.op('test_pack',w,pack=self.pack)
        with patch.object(self.protocol,'_patch',side_effect=ProtocolError('fixture interrupted patch')):
            (self.project/'other.txt').write_text('dirty')
            with self.assertRaisesRegex(ProtocolError,'interrupted patch'):
                self.op('prepare',w)
        retained = self.protocol.list()[0]
        self.assertTrue(Path(retained['workspace']).is_dir())
        self.assertEqual(retained['status'],'needs_attention')
        cancelled = self.op('cancel',retained)
        with self.assertRaisesRegex(ProtocolError,'Preparation interrupted'):
            self.op('resume',cancelled)
        self.assertEqual((self.project/'other.txt').read_text(),'dirty')
    def test_interrupted_apply_intent_never_replays(self):
        w = self.checked()
        w = self.op('integration_check',w)
        payload = {'id':w['id'],'version':w['version'],'approve':True,'integration_hash':w['integration_hash']}
        with patch.object(self.protocol,'_copy_changes',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.protocol.mutate('apply',payload,'crashed-apply',trusted=True)
        with self.assertRaisesRegex(ProtocolError,'Interrupted'):
            self.protocol.mutate('apply',payload,'crashed-apply',trusted=True)
        self.assertEqual((self.project/'result.txt').read_text(),'old')

    def test_global_filter_and_info_attributes_cannot_execute_during_prepare(self):
        import shlex
        home = self.root / 'filter-home'; home.mkdir()
        marker = self.root / 'forbidden-global-filter'
        program = 'touch ' + shlex.quote(str(marker)) + '; cat'
        subprocess.run(['git','config','--file',str(home/'.gitconfig'),'filter.review.smudge',program],check=True)
        subprocess.run(['git','config','--file',str(home/'.gitconfig'),'filter.review.clean',program],check=True)
        (self.project/'.git/info/attributes').write_text('result.txt filter=review\n')
        w = self.create(); self.op('test_pack', w, pack=self.pack)
        with patch.dict(os.environ, {'HOME':str(home),'USERPROFILE':str(home)}):
            w = self.op('prepare',w)
            self.assertEqual(w['status'],'unsupported')
            self.assertIn('Effective Git checkout filters',w['message'])
        self.assertFalse(marker.exists())
    def test_new_main_filter_after_prepare_blocks_combined_checkout_and_inspection(self):
        import shlex
        w = self.checked()
        marker = self.root / 'forbidden-new-filter'
        (self.project/'.gitattributes').write_text('result.txt filter=review\n')
        self.git('add','.gitattributes'); self.git('commit','-m','new filter attrs')
        program = 'touch ' + shlex.quote(str(marker)) + '; cat'
        self.git('config','filter.review.smudge',program);self.git('config','filter.review.clean',program)
        with self.assertRaisesRegex(ProtocolError,'Effective Git checkout filters'):
            self.op('integration_check',w)
        snapshot(self.project)  # Inspection also disables clean filters.
        self.assertFalse(marker.exists())
        visible=self.protocol.list()[0]
        self.assertEqual(visible['status'],'needs_attention')
        self.assertIsNone(visible['integration'])
    def test_nested_and_global_attributes_filter_gate_and_unused_global_filter_allowed(self):
        import shlex
        home=self.root/'attribute-home';home.mkdir()
        marker=self.root/'forbidden-attributes'
        program='touch '+shlex.quote(str(marker))+'; cat'
        config=home/'.gitconfig'
        subprocess.run(['git','config','--file',str(config),'filter.review.smudge',program],check=True)
        subprocess.run(['git','config','--file',str(config),'filter.lfs.smudge',program],check=True)
        w=self.create();self.op('test_pack',w,pack=self.pack)
        with patch.dict(os.environ,{'HOME':str(home),'USERPROFILE':str(home)}):
            w=self.op('prepare',w)
            self.assertEqual(w['status'],'implementing') # Unused global configuration is harmless.
        self.assertFalse(marker.exists())
        self.op('cancel',w)
        attributes=home/'global-attributes';attributes.write_text('result.txt filter=review\n')
        subprocess.run(['git','config','--file',str(config),'core.attributesfile',str(attributes)],check=True)
        w=self.create();self.op('test_pack',w,pack=self.pack)
        with patch.dict(os.environ,{'HOME':str(home),'USERPROFILE':str(home)}):
            w=self.op('prepare',w)
            self.assertEqual(w['status'],'unsupported')
        self.assertFalse(marker.exists())
    def test_protocol_git_never_runs_hooks_external_diff_or_textconv(self):
        import shlex
        marker=self.root/'forbidden-git-driver'
        program='touch '+shlex.quote(str(marker))+'; cat'
        hook=self.project/'.git/hooks/post-checkout'
        hook.write_text('#!/bin/sh\n'+program+'\n');hook.chmod(0o700)
        self.git('config','diff.external',program)
        self.git('config','diff.review.textconv',program)
        (self.project/'.git/info/attributes').write_text('result.txt diff=review\n')
        w=self.checked()
        self.op('integration_check',w)
        self.assertFalse(marker.exists())


class ProtocolHTTPTests(unittest.TestCase):
    def test_exact_trusted_ui_boundary_registry_version_and_no_provider_execution(self):
        import http.client
        import threading
        from ustam.core import Hub
        from ustam.server import UstamServer
        class OfflineAdapters:
            def close(self): pass
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); project=root/'project';project.mkdir()
            hub=Hub(root/'state',adapters=OfflineAdapters())
            hub.projects({'action':'add','path':str(project)})
            entry=hub.bootstrap()['projects'][0]
            c={'scope':'Routine result','criteria':['Good result'],'files':['result.txt'],'commands':[]}
            w=hub.works.mutate('create',{'provider':'codex','project_id':entry['id'],'path':str(project),'contract':c},'http-create')
            c['scope']='Materially revised scope'
            w=hub.works.mutate('revise',{'id':w['id'],'version':w['version'],'contract':c},'http-revise')
            server=UstamServer(('127.0.0.1',0),hub)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(method,path,body=None,headers=None):
                conn=http.client.HTTPConnection(*server.server_address,timeout=3)
                conn.request(method,path,body=body,headers=headers or {})
                response=conn.getresponse();value=json.loads(response.read());conn.close()
                return response.status,value
            try:
                code,bootstrap=request('GET','/api/bootstrap')
                headers={'Content-Type':'application/json','Origin':server.origin,'X-Ustam-CSRF':bootstrap['csrf']}
                body={'action':'approve','id':w['id'],'version':w['version'],'contract_hash':w['contract_hash'],'operation_id':'http-approve'}
                self.assertEqual(request('POST','/api/works',json.dumps(body),{})[0],403)
                self.assertEqual(request('POST','/api/works',json.dumps(body),dict(headers,Origin='https://untrusted.invalid'))[0],403)
                self.assertEqual(request('POST','/api/works',json.dumps(dict(body,version=1,operation_id='http-stale-version')),headers)[0],400)
                code,result=request('POST','/api/works',json.dumps(body),headers)
                self.assertEqual(code,200,result)
                self.assertEqual(result['work']['approval_basis'],'trusted_ustam_control')
                self.assertEqual(request('POST','/api/works',json.dumps(body),headers)[1],result)
                self.assertEqual(request('GET','/api/works')[1]['works'][0]['id'],w['id'])
                hub.projects({'action':'remove','id':entry['id']})
                self.assertEqual(request('GET','/api/works')[1]['works'],[])
                self.assertEqual(request('POST','/api/works',json.dumps(dict(body,action='cancel',operation_id='removed-cancel')),headers)[0],400)
            finally:
                server.shutdown();server.server_close();thread.join();hub.close()

class ProtocolThreeProviderPipelineTests(unittest.TestCase):
    def test_native_public_bridge_all_six_features_with_trusted_local_surface(self):
        from ustam.core import Hub
        class OfflineAdapters:
            def close(self): pass
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp).resolve()
            if sys.platform=='darwin': state=home/'Library/Application Support/Ustam'
            elif os.name=='nt': state=home/'AppData/Local/Ustam'
            else: state=home/'.config/ustam'
            project=home/'project';project.mkdir()
            def git(*args):
                return subprocess.run(['git',*args],cwd=project,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
            git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            (project/'baseline.txt').write_text('user context');git('add','.');git('commit','-m','fixture')
            hub=Hub(state,adapters=OfflineAdapters());hub.projects({'action':'add','path':str(project)})
            env=dict(os.environ,HOME=str(home),USERPROFILE=str(home),LOCALAPPDATA=str(home/'AppData/Local'));env.pop('XDG_CONFIG_HOME',None)
            def native(provider,action,payload):
                result=subprocess.run([sys.executable,'-m','ustam','--work-protocol',provider,action,'--project',str(project),'--operation-id',uuid.uuid4().hex],
                                      input=json.dumps(payload),text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=source,env=env,timeout=15)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return json.loads(result.stdout)['work']
            def control(action,w,**fields):
                return hub.work_action(dict(action=action,id=w['id'],version=w['version'],operation_id=uuid.uuid4().hex,**fields))['work']
            try:
                for provider in ('codex','claude','opencode'):
                    with self.subTest(provider=provider):
                        filename=provider+'.json'
                        command=[sys.executable,'-c','import json; from pathlib import Path; assert json.loads(Path('+repr(filename)+').read_text()) == {"ok":True}']
                        c={'scope':'Publish agreed JSON','criteria':['Agreed JSON object'],'files':[filename],'commands':[command]}
                        w=native(provider,'create',{'contract':c})
                        self.assertTrue(w['approval_required'])
                        w=control('approve',w,contract_hash=w['contract_hash'])
                        request={'id':w['id'],'version':w['version'],'question':'Choose output shape','options':['Agreed object','Other']}
                        w=native(provider,'question',request);w=native(provider,'question',request)
                        self.assertEqual(len(w['questions']),1)
                        w=control('answer',w,question_id=w['questions'][0]['id'],answer='Agreed object')
                        base={'id':w['id'],'version':w['version']}
                        acceptance={'author':'acceptance_test_author','checks':[{'criterion':0,'kind':'json_equals','path':filename,'expected':{'ok':True}}],
                                    'good':{filename:'{"ok":true}'},'bad':[{filename:'{"ok":false}'}]}
                        w=native(provider,'test_pack',dict(base,pack=acceptance))
                        w=native(provider,'prepare',base)
                        (Path(w['workspace'])/filename).write_text('{"ok":true}')
                        w=native(provider,'freeze',base)
                        w=native(provider,'check',dict(base,role='verifier'))
                        self.assertEqual(w['result']['provenance'],'controller_observed')
                        self.assertEqual(w['result']['commands'][0]['exit_code'],0)
                        w=native(provider,'integration_check',base)
                        self.assertEqual(w['status'],'ready_to_apply')
                        self.assertEqual(w['integration']['commands'][0]['exit_code'],0)
                        w=control('apply',w,approve=True,integration_hash=w['integration_hash'])
                        self.assertEqual(w['status'],'applied')
                        self.assertEqual(json.loads((project/filename).read_text()),{'ok':True})
                self.assertEqual((project/'baseline.txt').read_text(),'user context')
                self.assertEqual(len(hub.work_list()['works']),3)
                # Captured wrapper project binding cannot be overwritten by another argument.
                bad=subprocess.run([sys.executable,'-m','ustam','--work-protocol','codex','list','--project',str(project),'--project='+str(home)],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=source,env=env)
                self.assertNotEqual(bad.returncode,0)
                self.assertIn('Repeated control option',bad.stderr)
            finally:
                hub.close()
