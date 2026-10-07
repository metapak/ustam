"""Pure worker policy, API save gating and native legacy reads; no paid calls."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from ustam.adapter_worker import Engine
from ustam.core import Hub
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'engine-sources/claude'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class ModelEffortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        policy=load('test_model_policy',SOURCE/'scripts/model_capabilities.py')
        with patch.dict(sys.modules,{'model_capabilities':policy}):
            cls.installer=load('test_claude_installer',SOURCE/'scripts/install.py')
        cls.console=load('test_claude_console',SOURCE/'.claude/tools/console_settings.py')

    def engine(self):
        engine=object.__new__(Engine);engine.provider='claude';engine.installer=self.installer
        return engine

    def team(self,model='claude-sonnet-5-5',effort='medium'):
        return {'id':'fixture','name':'Fixture','provider':'claude','chief':{'model':model,'effort':effort},'helpers':[{'id':'one','name':'Implementation','role':'implementer','model':model,'effort':effort}],'concurrency':1,'profile':'balanced'}

    def test_pure_worker_validation_does_not_select_or_touch_target(self):
        engine=self.engine()
        with patch.object(engine,'select',side_effect=AssertionError('must not select project')):
            team=self.team()
            self.assertTrue(engine.call('validate_team','not-a-project',{'chief':team['chief'],'helpers':team['helpers']})['valid'])
            with self.assertRaises(self.installer.InstallError):
                team['chief']['effort']='max';engine.call('validate_team','',{'chief':team['chief'],'helpers':team['helpers']})

    def test_save_rejects_invalid_and_legacy_load_is_unchanged(self):
        engine=self.engine()
        adapters=SimpleNamespace(validate_team=lambda provider,team:engine.validate_team({'chief':team['chief'],'helpers':team['helpers']}),close=lambda:None)
        with tempfile.TemporaryDirectory() as directory:
            hub=Hub(Path(directory)/'state',adapters=adapters)
            try:
                legacy=self.team('sonnet','medium')
                hub.store.update(lambda data:data['orchestras'].append(legacy))
                before=hub.store.read()
                self.assertEqual(hub.bootstrap()['orchestras'][0]['chief'],legacy['chief'])
                with self.assertRaises(self.installer.InstallError):hub.orchestras({'action':'save','orchestra':legacy})
                self.assertEqual(hub.store.read(),before)
                team=self.team();team['chief']['effort']='max'
                with self.assertRaises(self.installer.InstallError):hub.orchestras({'action':'save','orchestra':team})
                good=self.team('haiku','')
                self.assertEqual(hub.orchestras({'action':'save','orchestra':good})['orchestras'][0]['chief']['effort'],'')
            finally:hub.close()

    def test_catalog_per_role_support_is_authoritative_and_offline(self):
        engine=self.engine()
        catalog=engine.catalog();models={item['id']:item for item in catalog['models']}
        self.assertNotIn('max',models['claude-opus-5-5']['chief_efforts'])
        self.assertIn('max',models['claude-opus-5-5']['efforts'])
        self.assertEqual(models['haiku']['efforts'],[])
        self.assertEqual(models['sonnet']['effort_status'],'unresolved_alias')

    def test_native_missing_effort_and_old_roster_read_without_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory);agents=target/'.claude/agents';agents.mkdir(parents=True)
            (target/'.claude/settings.json').write_text(json.dumps({'model':'haiku'}))
            (agents/'implementer.md').write_text('---\nname: implementer\nmodel: haiku\n---\nPrompt\n')
            meta=target/self.installer.MANIFEST_RELATIVE;meta.parent.mkdir(parents=True)
            legacy={'id':'slot-01','role':'implementer','label':'Legacy','model':'sonnet','effort':'medium'}
            meta.write_text(json.dumps({'schema':1,'files':{},'roster':[legacy]}))
            before={str(p):p.read_bytes() for p in target.rglob('*') if p.is_file()}
            read=self.console.Settings(self.installer,SOURCE,target).read()
            self.assertEqual(read['routing']['owner']['effort'],'')
            self.assertEqual(read['routing']['implementer']['effort'],'')
            self.assertEqual(read['roster'],[legacy])
            self.assertTrue(read['model_catalog']['models'])
            self.assertEqual(before,{str(p):p.read_bytes() for p in target.rglob('*') if p.is_file()})

if __name__=='__main__':unittest.main()
