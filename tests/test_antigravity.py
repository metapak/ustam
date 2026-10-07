"""Offline fourth-provider tests. No model calls or user project mutations."""
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

from ustam.core import Hub
from ustam.jobs import JobManager
from ustam.protocol import WorkProtocol, ProtocolError
from ustam.runtime import Runtime, RuntimeAttention, resolve_cli
from ustam.usage_baseline import evidence

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'engine-sources/antigravity'

def load():
    if not (SOURCE / 'scripts/console_settings.py').is_file():
        raise AssertionError('Antigravity in-repository engine source is missing')
    spec = importlib.util.spec_from_file_location('antigravity_install_fixture', SOURCE / 'scripts/install.py')
    constants = importlib.util.module_from_spec(spec); spec.loader.exec_module(constants)
    spec = importlib.util.spec_from_file_location('antigravity_settings_fixture', SOURCE / 'scripts/console_settings.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'install': constants}): spec.loader.exec_module(module)
    return module

module = load()

def team(model='flash', effort=''):
    return {'provider': 'antigravity', 'name': 'Fixture team', 'chief': {'model': 'pro', 'effort': ''}, 'helpers': [{'id': 'one', 'role': 'implementer', 'name': 'Implementation', 'model': model, 'effort': effort}], 'concurrency': 1, 'profile': 'balanced'}

SUPPORTED = {'status': 'supported', 'version': '1.2.16', 'reason': 'fixture help verified'}

class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.settings = module.Settings(self.project, [sys.executable, '-I', '-c', 'raise SystemExit(2)', '--work-protocol', 'antigravity'])
        self.probe = patch.object(module, 'probe', return_value=SUPPORTED); self.probe.start()
    def tearDown(self):
        self.probe.stop(); self.temp.cleanup()
    def apply(self, value=None):
        p = self.settings.preview(value or team())
        return self.settings.apply({'preview_id': p['preview_id']})
    def test_clean_reinstall_restore_and_provider_coexistence(self):
        protected = {'AGENTS.md': b'Codex routing\n', '.agents/skills/bounded-orchestrator/SKILL.md': b'codex', '.agents/rules/custom.md': b'user', '.claude/settings.json': b'{}', '.opencode/opencode.jsonc': b'{}', '.gemini/user.md': b'user'}
        for n, data in protected.items():
            p = self.project / n; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
        preview = self.settings.preview(team())
        self.assertFalse((self.project / '.antigravity').exists())
        result = self.settings.apply({'preview_id': preview['preview_id']})
        self.assertTrue(result['verified']); self.assertEqual([x['phase'] for x in result['phases']], ['preflight', 'backup', 'applied', 'verified'])
        first = module.read(self.project, module.MANIFEST)
        manifest = json.loads(first)
        self.assertTrue(evidence(self.project, 'antigravity')['installed'])
        self.apply(team('inherit'))
        self.assertEqual(json.loads(module.read(self.project, module.MANIFEST))['installed_at'], manifest['installed_at'])
        self.settings.restore({}); self.assertEqual(module.read(self.project, module.MANIFEST), first)
        self.settings.restore({}); self.assertIsNone(module.read(self.project, module.MANIFEST))
        for n, data in protected.items(): self.assertEqual((self.project / n).read_bytes(), data)
    def test_source_owned_hashes_and_changed_files_are_refused(self):
        self.apply(); p = self.project / '.agents/agents/ustam-antigravity-helper-01.md'; p.write_text('user edit')
        self.assertFalse(evidence(self.project, 'antigravity')['installed'])
        self.assertFalse(self.settings.inspect()['installed'])
        with self.assertRaisesRegex(ValueError, 'changed or incomplete'): self.settings.preview(team())
        with self.assertRaisesRegex(ValueError, 'changed or incomplete'): self.settings.restore({})
        self.assertEqual(p.read_text(), 'user edit')
    def test_stale_preview_and_namespace_collision(self):
        p = self.settings.preview(team()); path = self.project / '.agents/rules/ustam-antigravity.md'; path.parent.mkdir(parents=True); path.write_text('user')
        with self.assertRaisesRegex(ValueError, 'collision'): self.settings.apply({'preview_id': p['preview_id']})
        self.assertEqual(path.read_text(), 'user')
        with self.assertRaisesRegex(ValueError, 'Fresh'): self.settings.apply({'preview_id': p['preview_id']})
    def test_symlink_directory_file_backup_and_changed_project_refused(self):
        outside = self.root / 'outside'; outside.mkdir()
        (self.project / '.agents').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'): self.settings.preview(team())
        self.assertEqual(list(outside.iterdir()), [])
        (self.project / '.agents').unlink(); self.apply()
        m = self.settings.manifest(); backup = self.project / module.META / 'backups' / (m['backup_id'] + '.json')
        backup.unlink(); backup.symlink_to(outside / 'private')
        with self.assertRaisesRegex(ValueError, 'Symlink'): self.settings.restore({})
        moved = self.root / 'moved'; self.project.rename(moved); self.project.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'identity'): self.settings.preview(team())
    def test_failure_after_replace_restores_actual_mutation_before_claiming_rollback(self):
        p = self.settings.preview(team()); original_replace, original_sync = os.replace, os.fsync
        armed = [False, False]
        def replace(src,dst,*args,**kwargs):
            result = original_replace(src,dst,*args,**kwargs)
            if str(dst) == 'ustam-antigravity-helper-01.md': armed[0] = True
            return result
        def sync(fd):
            if armed[0] and not armed[1]: armed[1] = True; raise OSError('postmutation fsync fixture')
            return original_sync(fd)
        with patch.object(module.os,'replace',side_effect=replace), patch.object(module.os,'fsync',side_effect=sync):
            with self.assertRaises(module.TransactionError) as error: self.settings.call('apply',{'preview_id':p['preview_id']})
        self.assertTrue(armed[1]); self.assertEqual(error.exception.recovery_status,'rolled_back')
        self.assertFalse(any(p.is_file() for p in (self.project/'.agents').rglob('*')))

    def test_failure_after_unlink_restores_actual_mutation_before_claiming_rollback(self):
        self.apply(); before = {p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        original_unlink, original_sync = os.unlink, os.fsync; armed = [False,False]
        def unlink(path,*args,**kwargs):
            result = original_unlink(path,*args,**kwargs)
            if str(path) == 'ustam-antigravity-helper-01.md': armed[0] = True
            return result
        def sync(fd):
            if armed[0] and not armed[1]: armed[1] = True; raise OSError('postmutation fsync fixture')
            return original_sync(fd)
        with patch.object(module.os,'unlink',side_effect=unlink), patch.object(module.os,'fsync',side_effect=sync):
            with self.assertRaises(module.TransactionError) as error: self.settings.call('restore',{})
        self.assertTrue(armed[1]); self.assertEqual(error.exception.recovery_status,'rolled_back')
        self.assertEqual(before,{p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_project_inode_replacement_invalidates_preview_and_transferred_preview(self):
        p = self.settings.preview(team()); pending = self.settings.pending
        original = self.root / 'original'; self.project.rename(original); self.project.mkdir()
        with self.assertRaisesRegex(ValueError,'identity changed'): self.settings.apply({'preview_id':p['preview_id']})
        fresh = module.Settings(self.project,self.settings.bridge_command)
        fresh.pending = pending
        with self.assertRaisesRegex(ValueError,'identity changed'): fresh.apply({'preview_id':p['preview_id']})
        self.assertEqual(list(self.project.iterdir()),[])
        with self.assertRaisesRegex(ValueError,'Fresh'): module.Settings(self.project,self.settings.bridge_command).apply({'preview_id':p['preview_id']})

    def test_restore_inode_replacement_never_mutates_new_project_copy(self):
        import shutil
        self.apply(); original = self.root / 'original'; self.project.rename(original); shutil.copytree(original,self.project)
        before = {p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError,'identity changed'): self.settings.restore({})
        fresh = module.Settings(self.project,self.settings.bridge_command)
        self.assertFalse(evidence(self.project,'antigravity')['installed'])
        with self.assertRaisesRegex(ValueError,'identity changed'): fresh.restore({})
        with self.assertRaisesRegex(ValueError,'identity changed'): fresh.preview(team())
        self.assertEqual(before,{p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_windows_reparse_attribute_is_refused_even_without_symlink_flag(self):
        from types import SimpleNamespace
        path = self.project / '.agents'; path.mkdir()
        original = Path.lstat
        def attributes(p, *args, **kwargs):
            return SimpleNamespace(st_file_attributes=0x400) if p == path else original(p,*args,**kwargs)
        with patch.object(Path,'lstat',attributes):
            with self.assertRaisesRegex(ValueError,'reparse'): module.safe(self.project,'.agents/rules/ustam-antigravity.md')

    def test_incomplete_install_does_not_silently_repair(self):
        self.apply(); (self.project / '.antigravity/tools/work_protocol.py').unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete'): self.settings.preview(team())
    def test_apply_failure_rolls_back_and_retains_receipt(self):
        p = self.settings.preview(team()); original = module.write
        def failing(root, name, data, expected):
            if name.endswith('/work_protocol.py') and data is not None: raise OSError('fixture failure')
            return original(root, name, data, expected)
        with patch.object(module, 'write', side_effect=failing):
            with self.assertRaisesRegex(ValueError, 'rollback completed'): self.settings.apply({'preview_id': p['preview_id']})
        self.assertIsNone(module.read(self.project, module.MANIFEST))
        self.assertEqual(len(list((self.project / module.META / 'backups').glob('*.json'))), 1)
        self.assertFalse((self.project / '.agents/rules/ustam-antigravity.md').exists())
    def test_apply_failure_preserves_concurrent_user_edit(self):
        p = self.settings.preview(team()); original = module.write
        def failing(root, name, data, expected):
            if name.endswith('/work_protocol.py') and data is not None:
                (self.project / '.agents/rules/ustam-antigravity.md').write_text('concurrent user edit')
                raise OSError('fixture failure')
            return original(root, name, data, expected)
        with patch.object(module, 'write', side_effect=failing):
            with self.assertRaisesRegex(ValueError, 'conflicts retained'): self.settings.apply({'preview_id': p['preview_id']})
        self.assertEqual((self.project / '.agents/rules/ustam-antigravity.md').read_text(), 'concurrent user edit')
    def test_missing_cli_preflight_no_write_and_reprobe_before_apply(self):
        with patch.object(module, 'probe', return_value={'status': 'missing', 'version': None, 'reason': 'CLI missing'}):
            self.assertEqual(self.settings.inspect()['support_matrix']['cli']['status'], 'missing')
            with self.assertRaisesRegex(ValueError, 'CLI missing'): self.settings.preview(team())
        self.assertEqual(list(self.project.iterdir()), [])
        p = self.settings.preview(team())
        with patch.object(module, 'probe', return_value={'status': 'unsupported', 'version': '1.0.0', 'reason': 'CLI changed'}):
            with self.assertRaisesRegex(ValueError, 'CLI changed'): self.settings.apply({'preview_id': p['preview_id']})
        self.assertEqual(list(self.project.iterdir()), [])
    def test_model_effort_and_concurrency_no_invented_mapping(self):
        for value in (team('arbitrary-model'), team('flash', 'medium'), dict(team(), concurrency=2)):
            with self.assertRaises(ValueError): self.settings.preview(value)
        catalog = self.settings.models(); self.assertEqual([x['id'] for x in catalog['models']], ['inherit', 'flash', 'pro'])
        self.assertEqual(catalog['efforts'], []); self.assertFalse(catalog['capabilities']['jobs'])
        self.assertEqual(self.settings.call('usage', {})['status'], 'unsupported')
        self.assertEqual(self.settings.call('usage', {})['totals'], {})
        self.assertFalse(Runtime.job_capabilities('antigravity')['enabled'])
        with self.assertRaises(RuntimeAttention): Runtime(self.root / 'runtime').prepare({'provider':'antigravity'})
    def test_native_documents_route_only_antigravity_and_no_deprecated_workflows(self):
        self.apply()
        rule = (self.project / '.agents/rules/ustam-antigravity.md').read_text()
        owner = (self.project / '.agents/agents/ustam-antigravity-owner.md').read_text()
        helper = (self.project / '.agents/agents/ustam-antigravity-helper-01.md').read_text()
        self.assertIn('trigger: always_on', rule); self.assertIn('prompt conventions', rule)
        self.assertIn('"invoke_subagent"', owner); self.assertNotIn('effort:', helper)
        self.assertIn('commandExecutionPolicy: "off"', helper)
        self.assertFalse((self.project / '.agents/workflows').exists())
    def test_all_fifty_slots_and_reduced_roster_removes_only_owned_agents(self):
        t = team(); t['helpers'] = [dict(t['helpers'][0], id=str(i)) for i in range(50)]
        self.apply(t); self.assertTrue((self.project / '.agents/agents/ustam-antigravity-helper-50.md').exists())
        self.apply(); self.assertFalse((self.project / '.agents/agents/ustam-antigravity-helper-50.md').exists())
        self.settings.restore({}); self.assertTrue((self.project / '.agents/agents/ustam-antigravity-helper-50.md').exists())

    def test_backup_tamper_and_stale_preview_leave_user_files_intact(self):
        self.apply(); m = self.settings.manifest()
        backup = self.project / module.META / 'backups' / (m['backup_id'] + '.json')
        value = json.loads(backup.read_text()); value['manifest_before'] = 'e30='
        backup.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'integrity'): self.settings.restore({})
        self.assertTrue(self.settings.manifest())
        p = self.settings.preview(team())
        self.settings.pending = (p['preview_id'], 0, team(), {})
        with self.assertRaisesRegex(ValueError, 'Fresh'): self.settings.apply({'preview_id': p['preview_id']})

    def test_native_fixed_bridge_rejects_forged_approval_and_project_import(self):
        from ustam import native_protocol
        from ustam.protocol import digest
        state = self.root / 'hub-state'; state.mkdir()
        (state / 'hub.json').write_text(json.dumps({'projects':[{'id':'registered','path':str(self.project)}]}))
        # Source argv uses isolated Python and a fixed trusted hub path/state fixture.
        code = 'import sys; sys.path.insert(0, ' + repr(str(ROOT)) + '); from pathlib import Path; from ustam import native_protocol; native_protocol.state_directory=lambda:Path(' + repr(str(state)) + '); raise SystemExit(native_protocol.main("antigravity"))'
        self.settings.bridge_command = [sys.executable,'-I','-c',code,'--project',str(self.project)]
        self.apply()
        tools = self.project / '.antigravity/tools'
        (tools/'subprocess.py').write_text('raise RuntimeError("project import must never run")')
        (self.project/'ustam.py').write_text('raise RuntimeError("project import must never run")')
        bridge = [sys.executable,'-I',str(tools/'work_protocol.py')]
        result = subprocess.run([*bridge,'list'],cwd=self.project,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(json.loads(result.stdout)['works'],[])
        denied = subprocess.run([*bridge,'apply','--operation-id','forged'],cwd=self.project,input='{}',capture_output=True,text=True)
        self.assertNotEqual(denied.returncode,0)
        repeated = subprocess.run([*bridge,'list','--project',str(self.root)],cwd=self.project,capture_output=True,text=True)
        self.assertNotEqual(repeated.returncode,0)
        nonisolated = subprocess.run([sys.executable,str(tools/'work_protocol.py'),'list'],cwd=self.project,capture_output=True,text=True)
        self.assertNotEqual(nonisolated.returncode,0); self.assertIn('Python -I',nonisolated.stderr)

class SupportTests(unittest.TestCase):
    def test_read_only_version_help_probe_accepts_newer_rejects_old_unknown_missing_flags(self):
        root = Path(tempfile.gettempdir()) / 'different-project'
        cases = [('1.2.16', '--agent --model --output-format agents', 'supported'), ('1.3.0', '--agent --model --output-format agents', 'supported'), ('1.2.15', '', 'unsupported'), ('unknown', '', 'unsupported'), ('1.3.0', '--model', 'unsupported')]
        for version, help_text, expected in cases:
            with self.subTest(version=version, help=help_text), patch.object(module.shutil, 'which', return_value='/usr/local/bin/agy'), patch.object(module.subprocess, 'run', side_effect=[subprocess.CompletedProcess([],0,version.encode(),b''),subprocess.CompletedProcess([],0,help_text.encode(),b'')]) as run:
                self.assertEqual(module.probe(root)['status'], expected)
                self.assertTrue(all(c.args[0][1] in ('--version','--help') for c in run.call_args_list))
    def test_cli_help_on_stderr_is_a_supported_successful_local_probe(self):
        with patch.object(module.shutil,'which',return_value='/usr/local/bin/agy'), patch.object(module.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,b'1.2.16',b''),subprocess.CompletedProcess([],0,b'',b'--agent --model --output-format agents')]):
            self.assertEqual(module.probe(Path('/fixture-project'))['status'],'supported')

    def test_cli_resolver_fixed_agy_name_and_project_local_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); bins = root / 'bin'; bins.mkdir(); project = root / 'project'; project.mkdir()
            cli = bins / ('agy.exe' if os.name == 'nt' else 'agy'); cli.write_text('fixture'); cli.chmod(0o755)
            with patch('ustam.runtime._known_cli_bins', return_value=[]):
                self.assertEqual(resolve_cli('antigravity', project, {'PATH':str(bins)}), str(cli.resolve()))
                with self.assertRaises(RuntimeAttention): resolve_cli('antigravity', root, {'PATH':str(bins)})

class HubTests(unittest.TestCase):
    def test_provider_catalog_saved_team_usage_and_per_project_results(self):
        class Adapters:
            def preview(self, provider, target, payload):
                if Path(target).name == 'bad': raise ValueError('fixture preflight failure')
                info = Path(target).stat(); return {'preview_id':'fixture','root_identity':{'device':info.st_dev,'inode':info.st_ino}}
            def close(self): pass
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); hub = Hub(root / 'state', Adapters())
            try:
                for name in ('good','bad'):
                    project = root / name; project.mkdir(); hub.projects({'action':'add','path':str(project)})
                bootstrap = hub.bootstrap(); self.assertIn('antigravity', bootstrap['providers'])
                self.assertFalse(bootstrap['capabilities']['providers']['antigravity']['jobs']['enabled'])
                hub.orchestras({'action':'save','orchestra':team()})
                with self.assertRaises(ValueError): hub.orchestras({'action':'save','orchestra':team('unsupported')})
                ids = [p['id'] for p in bootstrap['projects']]
                result = hub.batch('preview', {'provider':'antigravity','project_ids':ids,'payload':team()})
                self.assertEqual([r['ok'] for r in result['results']], [True,False])
                usage = hub.usage('antigravity',ids[0]); self.assertEqual(usage['status'],'unsupported'); self.assertEqual(usage['totals'],{})
                jobs = JobManager(root / 'jobs', Adapters())
                with self.assertRaisesRegex(ValueError, 'unsupported'):
                    jobs.plan({'targets':[{'project_id':ids[0],'path':str(root/'good')}], 'task':'fixture', 'orchestra':team()})
                jobs.close()
            finally: hub.close()

    def test_hub_refuses_replaced_directory_before_any_new_adapter_worker(self):
        class Adapters:
            apply_calls = 0
            def preview(self, provider, target, payload):
                info = Path(target).stat(); return {'preview_id':'worker','root_identity':{'device':info.st_dev,'inode':info.st_ino}}
            def apply(self,*args): self.apply_calls += 1; return {'applied':True}
            def close(self): pass
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); project = root/'project'; project.mkdir(); adapters = Adapters(); hub = Hub(root/'state',adapters)
            try:
                hub.projects({'action':'add','path':str(project)}); ident = hub.bootstrap()['projects'][0]['id']
                preview = hub.batch('preview',{'provider':'antigravity','project_ids':[ident],'payload':team()})['results'][0]['preview_id']
                project.rename(root/'original'); project.mkdir()
                result = hub.apply({'preview_ids':[preview]})['results'][0]
                self.assertFalse(result['ok']); self.assertIn('identity changed',result['error']); self.assertEqual(adapters.apply_calls,0)
                self.assertEqual(list(project.iterdir()),[])
            finally: hub.close()

class ManualProtocolTests(unittest.TestCase):
    def test_antigravity_manual_candidate_requires_trusted_apply_and_observed_checks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project = root / 'project'; project.mkdir()
            for args in (['init'],['config','user.name','Fixture'],['config','user.email','fixture@example.invalid']): subprocess.run(['git',*args],cwd=project,check=True,capture_output=True)
            (project/'result.txt').write_text('old')
            for args in (['add','.'],['commit','-m','fixture']): subprocess.run(['git',*args],cwd=project,check=True,capture_output=True)
            protocol = WorkProtocol(root/'state'/'works')
            def op(action, work=None, trusted=False, **values):
                if work: values.update(id=work['id'],version=work['version'])
                return protocol.mutate(action,values,uuid.uuid4().hex,trusted=trusted)
            w = op('create',provider='antigravity',project_id='registered',path=str(project),contract={'scope':'Fixture','criteria':['success'],'files':['result.txt'],'commands':[]})
            with self.assertRaisesRegex(ProtocolError,'Acceptance pack'): op('prepare',w)
            w = op('test_pack',w,pack={'author':'acceptance_test_author','checks':[{'criterion':0,'kind':'contains','path':'result.txt','expected':'success'}],'good':{'result.txt':'success'},'bad':[{'result.txt':'old'}]})
            w = op('prepare',w); (Path(w['workspace'])/'result.txt').write_text('success'); w = op('freeze',w)
            w = op('reported_check',w,passed=True,summary='CLI exit 0 and soft denial')
            self.assertNotEqual(w['status'],'verified')
            w = op('check',w,role='verifier'); self.assertEqual(w['result']['status'],'passed')
            w = op('integration_check',w)
            with self.assertRaisesRegex(ProtocolError,'trusted'): op('apply',w)
            w = op('apply',w,trusted=True,approve=True,integration_hash=w['integration_hash'])
            self.assertEqual(w['status'],'applied'); self.assertEqual((project/'result.txt').read_text(),'success')
