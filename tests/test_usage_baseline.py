import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ustam.core import Hub
from ustam.usage_baseline import evidence

class Adapters:
    def preview(self, *args): return {'preview_id':'fixture'}
    def usage(self, *args): return {'records': [], 'totals': {}}
    def close(self): pass

class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.project=self.root/'project';self.project.mkdir()
        self.adapter=Adapters();self.hub=Hub(self.root/'state',self.adapter)
        self.hub.projects({'action':'add','path':str(self.project)});self.ident=self.hub.bootstrap()['projects'][0]['id']
        self.path=self.project/'.codex/.bounded-orchestrator/install.json'
    def tearDown(self):self.hub.close();self.temp.cleanup()
    def manifest(self,stamp='2026-10-01T00:00:00Z',snapshot=False):
        before=self.path.read_bytes() if self.path.exists() else None
        config=self.project/'.codex/config.toml';config.parent.mkdir(exist_ok=True);config.write_text('fixture')
        self.path.parent.mkdir(exist_ok=True)
        value={'schema':1,'installed_utc':stamp,'files':{'.codex/config.toml':{'owned':True,'sha256':hashlib.sha256(config.read_bytes()).hexdigest()}}}
        self.path.write_text(json.dumps(value))
        if snapshot:
            receipt={'schema':1,'files':{'.codex/.bounded-orchestrator/install.json':{'before':base64.b64encode(before).decode() if before else None,'after':hashlib.sha256(self.path.read_bytes()).hexdigest()}}}
            (self.path.parent/'console-restore.json').write_text(json.dumps(receipt))
        return before
    def entry(self):return self.hub.installations.read().get('usage_installations',{}).get(self.ident,{}).get('codex')
    def apply(self):
        preview=self.hub.batch('preview',{'provider':'codex','project_ids':[self.ident],'payload':{}})['results'][0]['preview_id']
        return self.hub.apply({'preview_ids':[preview]})['results'][0]
    def usage(self):return self.hub.usage('codex',self.ident)
    def test_snapshot_and_private_adoption_preserve_main_state(self):
        self.manifest();self.manifest('2026-10-04T00:00:00Z',True)
        before=self.hub.store.path.read_bytes();result=self.usage()
        self.assertEqual(result['installation_boundary']['started_at'],'2026-10-01T00:00:00+00:00')
        self.assertEqual(before,self.hub.store.path.read_bytes())
        first=self.entry();self.usage();self.assertEqual(first,self.entry())
    def test_malformed_snapshot_falls_back_and_tampering_fails_closed(self):
        self.manifest();self.manifest('2026-10-04T00:00:00Z',True)
        path=self.path.parent/'console-restore.json';value=json.loads(path.read_text());value['files']['.codex/.bounded-orchestrator/install.json']['after']='0'*64;path.write_text(json.dumps(value))
        self.assertEqual(evidence(self.project,'codex')['started_at'],'2026-10-04T00:00:00+00:00')
        (self.project/'.codex/config.toml').write_text('modified')
        self.assertFalse(evidence(self.project,'codex')['installed']);self.assertEqual(self.usage()['installation_boundary']['status'],'unverified')
    def test_apply_preserves_start_and_restore_restores_receipt(self):
        self.manifest();self.usage();prior=self.entry();raw=self.path.read_bytes()
        self.adapter.apply=lambda *args:self.manifest('2026-10-04T00:00:00Z',True) or {'applied':True}
        self.assertTrue(self.apply()['ok']);self.assertEqual(self.entry()['started_at'],prior['started_at'])
        self.adapter.restore=lambda *args:self.path.write_bytes(raw)
        result=self.hub.batch('restore',{'provider':'codex','project_ids':[self.ident],'payload':{}})
        self.assertTrue(result['results'][0]['ok']);self.assertEqual(self.entry(),prior)
    def test_failed_apply_and_restore_do_not_change_receipt(self):
        self.manifest();self.usage();prior=self.entry()
        def fail(*args):raise ValueError('fixture failure')
        self.adapter.apply=fail;self.assertFalse(self.apply()['ok']);self.assertEqual(self.entry(),prior)
        self.adapter.restore=fail;self.assertFalse(self.hub.batch('restore',{'provider':'codex','project_ids':[self.ident],'payload':{}})['results'][0]['ok']);self.assertEqual(self.entry(),prior)
    def test_receipt_persistence_failure_reports_provider_success(self):
        self.manifest();self.usage();prior=self.entry()
        self.adapter.apply=lambda *args:self.manifest('2026-10-04T00:00:00Z',True) or {}
        with patch.object(self.hub.installations,'update',side_effect=OSError('fixture receipt unavailable')):result=self.apply()
        self.assertFalse(result['ok']);self.assertTrue(result['provider_apply_succeeded']);self.assertEqual(result['receipt_status'],'receipt_failed');self.assertEqual(self.entry(),prior)
    def test_removal_and_new_generation_completion_time(self):
        self.manifest();self.usage();old=self.entry()
        self.adapter.restore=lambda *args:self.path.unlink()
        self.hub.batch('restore',{'provider':'codex','project_ids':[self.ident],'payload':{}});self.assertFalse(self.entry()['active'])
        self.adapter.apply=lambda *args:self.manifest('2026-10-04T00:00:00Z',True) or {}
        before=datetime.now(timezone.utc);result=self.apply();after=datetime.now(timezone.utc)
        self.assertTrue(result['ok']);stamp=datetime.fromisoformat(self.entry()['started_at']);self.assertLessEqual(before,stamp);self.assertLessEqual(stamp,after);self.assertNotEqual(self.entry()['generation'],old['generation'])
    def test_unproven_external_reinstallation_does_not_carry_old_start(self):
        self.manifest();self.usage();old=self.entry()
        self.manifest('2026-10-04T00:00:00Z');self.usage()
        self.assertNotEqual(self.entry()['generation'],old['generation']);self.assertEqual(self.entry()['started_at'],'2026-10-04T00:00:00+00:00')
    def test_bad_timestamp_symlink_and_absent_manifest_are_unverified(self):
        for stamp in ('not-a-date','2026-10-01T00:00:00','2099-01-01T00:00:00Z'):
            self.manifest(stamp);self.assertIsNone(evidence(self.project,'codex')['started_at'])
        self.path.unlink();self.path.symlink_to(self.root/'outside');self.assertFalse(evidence(self.project,'codex')['installed'])

    def test_usage_lock_is_bounded_and_failure_releases_metadata_locks(self):
        self.manifest()
        class Busy:
            def acquire(self,timeout):self.timeout=timeout;return False
        lock=Busy()
        with patch.object(self.hub,'_path_lock',return_value=lock):
            with self.assertRaisesRegex(ValueError,'already running'):self.usage()
        self.assertEqual(lock.timeout,45)
        with patch.object(self.hub.installations,'update',side_effect=OSError('failure')):
            with self.assertRaises(OSError):self.usage()
        self.assertEqual(self.usage()['installation_boundary']['status'],'verified')

    def test_every_snapshot_after_and_previous_owned_before_must_match(self):
        self.manifest();self.manifest('2026-10-04T00:00:00Z',True)
        path=self.path.parent/'console-restore.json';valid=json.loads(path.read_text())
        config='.codex/config.toml';correct=hashlib.sha256(b'fixture').hexdigest()
        for entry in ({'after':'0'*64,'before':base64.b64encode(b'fixture').decode()},{'after':correct,'before':base64.b64encode(b'previous').decode()}):
            value=json.loads(json.dumps(valid));value['files'][config]=entry;path.write_text(json.dumps(value))
            proof=evidence(self.project,'codex');self.assertIsNone(proof['previous_sha']);self.assertEqual(proof['started_at'],'2026-10-04T00:00:00+00:00')
        value=json.loads(json.dumps(valid));value['files'][config]={'after':correct,'before':base64.b64encode(b'fixture').decode()};path.write_text(json.dumps(value))
        self.assertEqual(evidence(self.project,'codex')['started_at'],'2026-10-01T00:00:00+00:00')
        # With no changed config entry, its live bytes must match the prior owned digest.
        path.write_text(json.dumps(valid));self.assertEqual(evidence(self.project,'codex')['started_at'],'2026-10-01T00:00:00+00:00')
        previous=json.loads(base64.b64decode(valid['files']['.codex/.bounded-orchestrator/install.json']['before']));previous['files'][config]['sha256']='0'*64
        valid['files']['.codex/.bounded-orchestrator/install.json']['before']=base64.b64encode(json.dumps(previous).encode()).decode();path.write_text(json.dumps(valid))
        self.assertEqual(evidence(self.project,'codex')['started_at'],'2026-10-04T00:00:00+00:00')
