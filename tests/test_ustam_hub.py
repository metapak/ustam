import sys
import unittest

if sys.version_info < (3, 11):
    raise unittest.SkipTest(
        "Ustam hub tests require the bundled Python 3.11+ runtime; legacy provider Python 3.10 CI remains supported"
    )

import http.client
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
import subprocess
from types import SimpleNamespace
from ustam.core import Hub
from ustam.discovery import discover, pick_directory
from ustam.server import UstamServer
from ustam.storage import StateStore

class FakeAdapters:
    def __init__(self): self.calls = []
    def preview(self, provider, target, payload):
        self.calls.append(('preview', provider, target, payload))
        return {'preview_id': 'worker-id', 'diff': 'proposed'}
    def apply(self, provider, target, ident):
        self.calls.append(('apply', provider, target, ident)); return {'applied': True}
    def restore(self, provider, target, payload): return {'restored': True}
    def usage(self, provider, target): return {'available': False}
    def close(self): pass

class HubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / 'project'; self.target.mkdir()
        self.adapter = FakeAdapters()
        self.hub = Hub(self.root / 'state', self.adapter)
        self.hub.projects({'action': 'add', 'path': str(self.target)})
        self.ident = self.hub.bootstrap()['projects'][0]['id']
    def tearDown(self): self.hub.close(); self.temp.cleanup()
    def preview(self):
        return self.hub.batch('preview', {'provider':'codex', 'project_ids':[self.ident], 'payload':{}})['results'][0]['preview_id']
    def test_http_usage_preserves_typed_index_timeout_without_generic_retry_code(self):
        from ustam.adapters import AdapterError
        import hashlib
        config=self.target/'.codex/config.toml';config.parent.mkdir();config.write_text('fixture')
        manifest=self.target/'.codex/.bounded-orchestrator/install.json';manifest.parent.mkdir()
        manifest.write_text(json.dumps({'schema':1,'installed_utc':'2026-01-01T00:00:00Z','files':{'.codex/config.toml':{'owned':True,'sha256':hashlib.sha256(config.read_bytes()).hexdigest()}}}))
        server = UstamServer(('127.0.0.1', 0), self.hub)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for code, status, result_code in (('usage_index_timeout', 408, 'usage_index_timeout'), ('adapter_error', 400, 'invalid_request'), ('bounds',413,'bounds')):
                with patch.object(self.adapter, 'usage', side_effect=AdapterError('bounded timeout', code=code)):
                    conn = http.client.HTTPConnection(*server.server_address, timeout=3)
                    conn.request('GET', '/api/usage?project_id='+self.ident+'&provider=codex')
                    response = conn.getresponse()
                    self.assertEqual(response.status, status)
                    self.assertEqual(json.loads(response.read())['error']['code'], result_code)
                    conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_http_response_accepts_large_result_and_rejects_over16mib(self):
        from ustam import server as server_module
        self.assertEqual(server_module.MAX_BODY,1024*1024)
        server=UstamServer(('127.0.0.1',0),self.hub);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for size,expected in ((5184501,200),(server_module.MAX_RESPONSE+1,413)):
                result={'fixture':'x'*size}
                with patch.object(self.hub,'bootstrap',return_value=result):
                    conn=http.client.HTTPConnection(*server.server_address,timeout=5);conn.request('GET','/api/bootstrap');response=conn.getresponse();body=json.loads(response.read());conn.close()
                self.assertEqual(response.status,expected)
                if expected==200:self.assertEqual(len(body['fixture']),size)
                else:self.assertEqual(body['error']['code'],'bounds');self.assertNotIn('fixture',body)
        finally:server.shutdown();server.server_close();thread.join()

    def test_worker_bounds_survives_session_and_http_usage(self):
        import hashlib, io, queue
        from unittest.mock import Mock
        from ustam import adapter_worker
        from ustam.adapters import _Session
        config=self.target/'.codex/config.toml';config.parent.mkdir();config.write_text('fixture')
        manifest=self.target/'.codex/.bounded-orchestrator/install.json';manifest.parent.mkdir()
        manifest.write_text(json.dumps({'schema':1,'installed_utc':'2026-01-01T00:00:00Z','files':{'.codex/config.toml':{'owned':True,'sha256':hashlib.sha256(config.read_bytes()).hexdigest()}}}))
        request={'reqid':'a'*32,'method':'usage','target':str(self.target),'params':{}}
        worker=Mock(provider='codex',module=SimpleNamespace(usage=SimpleNamespace(UsageIndexTimeout=ValueError)))
        worker.call.return_value={'oversize':'x'*adapter_worker.MAX_RESPONSE}
        output=io.BytesIO()
        with patch.object(adapter_worker,'Engine',return_value=worker),patch.object(sys,'stdin',SimpleNamespace(buffer=io.BytesIO((json.dumps(request)+'\n').encode()))),patch.object(sys,'stdout',SimpleNamespace(buffer=output)):
            self.assertEqual(adapter_worker.main(['codex']),0)
        session=_Session.__new__(_Session);session.timeout=2;session.lock=threading.Lock();session.responses=queue.Queue()
        session.process=SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(output.getvalue()))
        session._read()
        server=UstamServer(('127.0.0.1',0),self.hub);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(self.adapter,'usage',side_effect=lambda provider,target:session.request('usage',str(target),{})),patch('ustam.adapters.uuid.uuid4',return_value=SimpleNamespace(hex='a'*32)):
                conn=http.client.HTTPConnection(*server.server_address,timeout=5)
                try:
                    conn.request('GET','/api/usage?project_id='+self.ident+'&provider=codex')
                    response=conn.getresponse();body=json.loads(response.read())
                finally:conn.close()
            self.assertEqual(response.status,413)
            self.assertEqual(body['error']['code'],'bounds')
            self.assertNotIn('result',body)
        finally:server.shutdown();server.server_close();thread.join()

    def test_preview_bound_consumed_and_stale(self):
        ident = self.preview()
        self.assertTrue(self.hub.apply({'preview_ids':[ident]})['results'][0]['ok'])
        self.assertFalse(self.hub.apply({'preview_ids':[ident]})['results'][0]['ok'])
        ident = self.preview()
        self.hub.defaults({'defaults': {'concurrency': 2}})
        self.assertFalse(self.hub.apply({'preview_ids':[ident]})['results'][0]['ok'])
        self.assertEqual(len([c for c in self.adapter.calls if c[0]=='apply']), 1)
    def test_metadata_never_mutates_project_and_persists(self):
        self.hub.defaults({'defaults': {'profile':'bounded'}})
        self.assertEqual(list(self.target.iterdir()), [])
        loaded = StateStore(self.root / 'state').read()
        self.assertEqual(loaded['defaults']['profile'], 'bounded')
        with self.assertRaises(ValueError): self.hub.defaults({'defaults':{}, 'revision':0})
        self.hub.projects({'action':'refresh','roots':[str(self.root)]})
        self.assertEqual(self.hub.bootstrap()['projects'][0]['id'], self.ident)
    def test_reject_unknown_target_and_cross_provider(self):
        with self.assertRaises(ValueError): self.hub.batch('preview', {'provider':'codex','project_ids':['arbitrary'],'payload':{}})
        with self.assertRaises(ValueError): self.hub.provider('copilot')
        orchestra = {'id':'x','name':'X','provider':'claude','chief':{'model':'m','effort':'high'},'helpers':[],'concurrency':1}
        with self.assertRaises(ValueError): self.hub.batch('preview', {'provider':'codex','project_ids':[self.ident],'payload':{'orchestra':orchestra}})
    def test_discovery_skips_symlinks_and_ignored_trees(self):
        good = self.target / 'good'; good.mkdir(); (good/'package.json').write_text('{}')
        ignored = self.target / 'node_modules'; ignored.mkdir(); (ignored/'package.json').write_text('{}')
        (self.target/'linked').symlink_to(good, target_is_directory=True)
        found = discover([str(self.target)])
        self.assertEqual([p['path'] for p in found], [str(good)])
    def test_active_job_excludes_apply(self):
        class Jobs:
            lock = threading.RLock()
            active = {str(self.target): 'active'}
        self.hub.jobs = Jobs()
        result = self.hub.apply({'preview_ids':[self.preview()]})['results'][0]
        self.assertFalse(result['ok'])
        self.assertIn('job is active', result['error'])
    def test_orchestra_edit_and_project_override_only_change_metadata(self):
        orchestra = {'id':'local','name':'Local','provider':'codex','chief':{'model':'test-model','effort':'medium'},'helpers':[{'id':'h','role':'implementer','name':'Worker','model':'test-model','effort':'high'}],'concurrency':1,'profile':'balanced'}
        self.hub.orchestras({'action':'save','orchestra':orchestra})
        orchestra['name'] = 'Renamed'
        self.hub.orchestras({'action':'save','orchestra':orchestra})
        self.hub.defaults({'project_id':self.ident,'override':{'orchestra_id':'local'}})
        self.assertEqual(list(self.target.iterdir()), [])
        state = self.hub.bootstrap()
        self.assertEqual(state['orchestras'][0]['name'],'Renamed')
        self.assertEqual(state['project_overrides'][self.ident]['orchestra_id'],'local')
        self.assertEqual(self.adapter.calls, [])

    def test_atomic_store_failed_write_keeps_revision_and_restart_state(self):
        before = self.hub.store.read()
        with patch('ustam.storage.os.replace', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError): self.hub.defaults({'defaults':{'new':True}})
        self.assertEqual(self.hub.store.read(),before)
        self.assertEqual(StateStore(self.root/'state').read(),before)
        self.assertEqual(list((self.root/'state').glob('.hub-*')), [])

    def test_expired_preview_never_applies(self):
        ident = self.preview()
        self.hub.previews[ident]['created'] -= 301
        result = self.hub.apply({'preview_ids':[ident]})['results'][0]
        self.assertFalse(result['ok'])
        self.assertFalse(any(call[0]=='apply' for call in self.adapter.calls))

    def test_project_identity_change_rejects_preview(self):
        ident = self.preview()
        moved = self.root/'original'
        self.target.rename(moved)
        replacement = self.root/'replacement'; replacement.mkdir()
        self.target.symlink_to(replacement, target_is_directory=True)
        result = self.hub.apply({'preview_ids':[ident]})['results'][0]
        self.assertFalse(result['ok'])
        self.assertFalse(any(call[0]=='apply' for call in self.adapter.calls))

    def test_canonical_duplicates_and_discovery_bounds(self):
        alias = self.root/'alias'; alias.symlink_to(self.target, target_is_directory=True)
        self.hub.projects({'action':'add','path':str(alias)})
        self.assertEqual(len(self.hub.bootstrap()['projects']),1)
        child = self.target/'child'; child.mkdir(); (child/'pyproject.toml').write_text('')
        deep = child/'deep'; deep.mkdir(); (deep/'package.json').write_text('{}')
        self.assertEqual(discover([str(self.target)],max_depth=0), [])
        with self.assertRaises(ValueError): discover([str(self.target)],max_directories=1)
        state = self.hub.bootstrap()
        with patch('ustam.core.discover', side_effect=ValueError('Discovery limit exceeded')):
            with self.assertRaises(ValueError): self.hub.projects({'action':'refresh','roots':[str(self.target)]})
        self.assertEqual(self.hub.bootstrap(),state)
        found = discover([str(self.target),str(alias)])
        self.assertEqual(len(found),2)

    def test_job_gateway_replaces_untrusted_paths_and_revalidates_start(self):
        class Jobs:
            def __init__(self): self.plan_payload=None; self.started=False
            def plan(self,payload): self.plan_payload=payload; return {'id':'job'}
            def get(self,ident): return {'project_id':self_ident,'path':str(self_target)}
            def start(self,ident): self.started=True; return {'id':ident}
        self_ident,self_target=self.ident,self.target
        self.hub.jobs=Jobs()
        self.hub.job_action({'action':'plan','provider':'codex','project_ids':[self.ident], 'targets':[{'path':'/untrusted'}],'path':'/untrusted','paths':['/untrusted'],'target':'/untrusted','task':'Review'})
        captured=self.hub.jobs.plan_payload
        self.assertEqual(captured['targets'],[{'project_id':self.ident,'path':str(self.target)}])
        self.assertFalse(any(key in captured for key in ('path','paths','target')))
        self.hub.projects({'action':'remove','id':self.ident})
        with self.assertRaises(ValueError): self.hub.job_action({'action':'start','id':'job'})
        self.assertFalse(self.hub.jobs.started)

    def test_two_stores_reject_stale_revision_and_reload_before_update(self):
        second=StateStore(self.root/'state')
        revision=second.read()['revision']
        self.hub.defaults({'defaults':{'first':'preserved'},'revision':revision})
        with self.assertRaises(ValueError): second.update(lambda data: data.update(defaults={'stale':True}),revision)
        self.assertEqual(second.read()['defaults'],{'first':'preserved'})
        second.update(lambda data: data.update(roots=['new-root']))
        self.assertEqual(self.hub.store.read()['defaults'],{'first':'preserved'})
        self.assertEqual(self.hub.store.read()['roots'],['new-root'])

    def test_two_processes_reject_stale_and_preserve_latest_metadata(self):
        code = """import json,sys
from ustam.storage import StateStore
store=StateStore(sys.argv[1]); revision=store.read()['revision']
print('ready',flush=True); sys.stdin.readline()
try:
 store.update(lambda data:data.update(defaults={'stale':True}),revision)
 print('unexpected',flush=True)
except ValueError:
 print('rejected',flush=True)
store.update(lambda data:data.update(roots=['child-root']))
print(json.dumps(store.read()),flush=True)
"""
        child=subprocess.Popen([sys.executable,'-c',code,str(self.root/'state')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'ready')
            self.hub.defaults({'defaults':{'first':'preserved'}})
            output,error=child.communicate('continue\n',timeout=10)
            self.assertEqual(child.returncode,0,error)
            lines=output.splitlines()
            self.assertEqual(lines[0],'rejected')
            latest=json.loads(lines[1])
            self.assertEqual(latest['defaults'],{'first':'preserved'})
            self.assertEqual(self.hub.store.read(),latest)
            self.assertEqual(latest['roots'],['child-root'])
        finally:
            if child.poll() is None: child.kill(); child.communicate()

    def test_project_remove_cannot_interleave_with_job_prepare(self):
        preparing,release,removing,removed=([threading.Event() for _ in range(4)])
        owner=self
        class Jobs:
            def __init__(self): self.lock=threading.RLock(); self.started=False
            def get(self,ident):
                with self.lock: return {'project_id':owner.ident,'path':str(owner.target)}
            def start(self,ident):
                with self.lock:
                    preparing.set()
                    if not release.wait(3): raise ValueError('Test prepare timed out')
                    self.started=True
                    return {'id':ident,'status':'running'}
        self.hub.jobs=Jobs(); failures=[]
        def start():
            try: self.hub.job_action({'action':'start','id':'job'})
            except Exception as error: failures.append(error)
        def remove():
            removing.set()
            try: self.hub.projects({'action':'remove','id':self.ident})
            except Exception as error: failures.append(error)
            finally: removed.set()
        starter=threading.Thread(target=start); remover=threading.Thread(target=remove)
        starter.start()
        try:
            self.assertTrue(preparing.wait(2))
            remover.start(); self.assertTrue(removing.wait(2))
            self.assertFalse(removed.wait(0.1))
            self.assertIn(self.ident,[entry['id'] for entry in self.hub.store.data['projects']])
        finally:
            release.set(); starter.join(3)
            if remover.ident is not None: remover.join(3)
        self.assertFalse(starter.is_alive()); self.assertFalse(remover.is_alive())
        self.assertEqual(failures,[])
        self.assertTrue(self.hub.jobs.started)
        self.assertTrue(removed.is_set())
        self.assertEqual(self.hub.store.read()['projects'],[])

    def test_http_batch_missing_registered_project_preserves_valid_sibling(self):
        missing=self.root/'missing'; missing.mkdir()
        self.hub.projects({'action':'add','path':str(missing)})
        missing_id=next(entry['id'] for entry in self.hub.bootstrap()['projects'] if entry['path']==str(missing))
        missing.rmdir()
        before=self.hub.store.read()
        server=UstamServer(('127.0.0.1',0),self.hub)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection(*server.server_address,timeout=3)
            conn.request(method,path,body,headers or {})
            response=conn.getresponse(); output=(response.status,json.loads(response.read())); conn.close(); return output
        try:
            _,bootstrap=request('GET','/api/bootstrap')
            headers={'Content-Type':'application/json','Origin':server.origin,'X-Ustam-CSRF':bootstrap['csrf']}
            status,result=request('POST','/api/preview',json.dumps({'provider':'codex','project_ids':[missing_id,self.ident],'payload':{}}),headers)
            self.assertEqual(status,200)
            self.assertEqual([row['ok'] for row in result['results']],[False,True])
            self.assertIn('unavailable',result['results'][0]['error'])
            self.assertEqual(result['results'][1]['project_id'],self.ident)
            self.assertTrue(result['results'][1]['preview_id'])
            self.assertEqual(len(self.adapter.calls),1)
            self.assertEqual(self.hub.store.read(),before)
            _,after=request('GET','/api/bootstrap')
            self.assertEqual(after['csrf'],bootstrap['csrf'])
            self.assertEqual(after['revision'],bootstrap['revision'])
            status,_=request('POST','/api/preview',json.dumps({'provider':'codex','project_ids':[self.ident,'unknown'],'payload':{}}),headers)
            self.assertEqual(status,400)
            self.assertEqual(len(self.adapter.calls),1)
            self.assertEqual(self.hub.store.read(),before)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_http_bootstrap_exposes_provider_job_limits_without_mutation(self):
        before=self.hub.store.read()
        server=UstamServer(('127.0.0.1',0),self.hub)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        def bootstrap():
            conn=http.client.HTTPConnection(*server.server_address,timeout=3)
            conn.request('GET','/api/bootstrap')
            response=conn.getresponse(); result=(response.status,json.loads(response.read())); conn.close(); return result
        try:
            status,result=bootstrap()
            self.assertEqual(status,200)
            self.assertFalse(result['capabilities']['jobs'])
            self.assertEqual(result['providers'],['codex','claude','opencode','antigravity'])
            self.assertEqual(result['capabilities']['native_picker']['endpoint'],'/api/projects/pick')
            for provider,concurrency in [('codex',10),('claude',20),('opencode',1)]:
                capability=result['capabilities']['providers'][provider]
                self.assertEqual(capability['max_concurrency'],concurrency)
                jobs=capability['jobs']
                self.assertEqual(jobs['max_helper_slots'],50)
                self.assertEqual(jobs['required_roles'],['explorer','implementer','verifier','reviewer'])
                self.assertEqual(jobs['duplicate_role_policy'],'first')
                self.assertEqual(jobs['helper_concurrency'],1)
                self.assertTrue(set(jobs['required_roles']).issubset(jobs['supported_roles']))
                if provider=='opencode':
                    self.assertEqual(jobs['execution_roles'],jobs['required_roles'])
                else:
                    self.assertEqual(jobs['execution_roles'],jobs['supported_roles'])
            self.hub.jobs=object()
            status,enabled=bootstrap()
            self.assertEqual(status,200)
            self.assertTrue(enabled['capabilities']['jobs'])
            self.assertEqual(enabled['capabilities']['providers'],result['capabilities']['providers'])
            self.assertEqual(self.hub.store.read(),before)
            self.assertEqual(self.adapter.calls,[])
            self.assertEqual(list(self.target.iterdir()),[])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_picker_cancel_selection_timeout_and_no_metadata_write(self):
        before = self.hub.bootstrap()
        with patch('ustam.core.pick_directory', return_value=None):
            self.assertEqual(self.hub.pick_project_directory({}), {'path':None,'cancelled':True})
        with patch('ustam.core.pick_directory', return_value=str(self.target)):
            self.assertEqual(self.hub.pick_project_directory({})['path'],str(self.target))
        with self.assertRaises(ValueError): self.hub.pick_project_directory({'command':'not allowed'})
        self.assertEqual(self.hub.bootstrap(),before)
        with patch('sys.platform','darwin'), patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='User canceled. (-128)')) as run:
            self.assertIsNone(pick_directory())
            self.assertEqual(run.call_args.args[0][0],'/usr/bin/osascript')
            self.assertNotIn('shell',run.call_args.kwargs)
        with patch('sys.platform','darwin'), patch('subprocess.run',side_effect=subprocess.TimeoutExpired('osascript',120)):
            with self.assertRaises(ValueError): pick_directory()

    def test_server_origin_csrf_and_host(self):
        server = UstamServer(('127.0.0.1',0), self.hub)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        host, port = server.server_address
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection(host,port,timeout=3)
            conn.request(method,path,body,headers or {})
            response = conn.getresponse(); result=(response.status,json.loads(response.read())); conn.close(); return result
        try:
            status, bootstrap = request('GET','/api/bootstrap')
            self.assertEqual(status,200)
            data = json.dumps({'defaults':{}})
            headers = {'Content-Type':'application/json','Origin':server.origin,'X-Ustam-CSRF':bootstrap['csrf']}
            self.assertEqual(request('POST','/api/defaults',data,headers)[0],200)
            before = self.hub.store.read()
            headers['Origin']='https://evil.invalid'
            self.assertEqual(request('POST','/api/defaults',data,headers)[0],403)
            headers['Origin']=server.origin; headers['X-Ustam-CSRF']='wrong'
            self.assertEqual(request('POST','/api/defaults',data,headers)[0],403)
            self.assertEqual(request('GET','/api/bootstrap',headers={'Host':'evil.invalid'})[0],403)
            headers['X-Ustam-CSRF']=bootstrap['csrf']
            headers['Host']='evil.invalid'
            self.assertEqual(request('POST','/api/defaults',data,headers)[0],403)
            headers.pop('Host')
            headers['Content-Length']='1048577'
            self.assertEqual(request('POST','/api/defaults',data,headers)[0],413)
            headers.pop('Content-Length')
            self.assertEqual(request('POST','/api/defaults','[]',headers)[0],400)
            self.assertEqual(self.hub.store.read(),before)
            with patch('ustam.core.pick_directory', return_value=None) as picker:
                self.assertEqual(bootstrap['capabilities']['native_picker']['endpoint'],'/api/projects/pick')
                self.assertEqual(request('POST','/api/projects/pick','{"kind":"directory"}',headers)[1]['cancelled'],True)
                headers['Origin']='https://evil.invalid'
                self.assertEqual(request('POST','/api/projects/pick','{}',headers)[0],403)
                self.assertEqual(picker.call_count,1)
            self.assertEqual(self.hub.store.read(),before)
        finally:
            server.shutdown(); server.server_close(); thread.join()

if __name__ == '__main__': unittest.main()
