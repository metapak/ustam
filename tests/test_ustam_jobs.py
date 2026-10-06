import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

if sys.version_info < (3, 11):
    raise unittest.SkipTest("Ustam hub requires Python 3.11 or newer")

from ustam.jobs import JobManager
from ustam.runtime import Runtime, RuntimeAttention, resolve_cli, cli_environment


class BlockingRuntime:
    def prepare(self, job, resume=False):
        pass
    def run(self, job, cancel, emit, resume=False):
        emit({'type': 'session', 'session_id': 'same-provider-session'})
        cancel.wait(3)
        return {'status': 'cancelled', 'message': 'Changes retained'}


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.manager = JobManager(self.root / 'state', runtime=BlockingRuntime())
    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()
    def payload(self, provider='codex'):
        return {'targets': [{'project_id': 'registered', 'path': str(self.project)}], 'task': 'Make a bounded change',
                'orchestra': {'provider': provider, 'chief': {'model': 'chief-model', 'effort': 'medium'},
                              'helpers': [{'role': role, 'model': role + '-model', 'effort': 'high'}
                                          for role in ('explorer', 'implementer', 'verifier', 'reviewer')]}}
    def wait(self, identifier):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = self.manager.get(identifier)
            if job['status'] not in ('running', 'cancelling'):
                return job
            time.sleep(.01)
        self.fail('Job did not stop')
    def test_plan_has_no_execution_and_requires_explicit_start(self):
        job = self.manager.plan(self.payload())
        self.assertEqual(job['status'], 'planned')
        self.assertEqual(job['plan']['kind'], 'proposed_plan')
        self.assertTrue(job['plan']['approval_required'])
        self.assertEqual(self.manager.active, {})
        self.assertEqual(job['events'], [])
        self.assertEqual(list(self.project.iterdir()), [])
    def test_project_alias_serialization_cancel_and_resume(self):
        first = self.manager.plan(self.payload())
        payload = self.payload()
        payload['targets'][0]['path'] += '/.'
        second = self.manager.plan(payload)
        self.manager.start(first['id'])
        self.assertTrue(self.manager.is_active(str(self.project)))
        with self.assertRaisesRegex(ValueError, 'active'):
            self.manager.start(second['id'])
        self.manager.cancel(first['id'])
        job = self.wait(first['id'])
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['session_id'], 'same-provider-session')
        self.manager.resume(first['id'])
        self.manager.cancel(first['id'])
        self.wait(first['id'])
    def test_restart_marks_interrupted_attention_and_metadata_private(self):
        job = self.manager.plan(self.payload())
        self.manager.jobs[job['id']].update(status='running', owner_pid=123456789)
        self.manager._save()
        other = JobManager(self.root / 'state', runtime=BlockingRuntime())
        self.assertEqual(other.get(job['id'])['status'], 'needs_attention')
        if os.name != 'nt':
            self.assertEqual(other.path.stat().st_mode & 0o777, 0o600)
        other.close()
    def test_fifty_slots_explicit_selection_not_silent(self):
        payload = self.payload()
        payload['orchestra']['helpers'] += [dict(payload['orchestra']['helpers'][1]) for _ in range(46)]
        job = self.manager.plan(payload)
        self.assertEqual(len(job['orchestra']['helpers']), 50)
        self.assertEqual(len(job['plan']['execution_helpers']), 4)
        self.assertIn('Other saved slots', job['plan']['helper_selection'])
    def test_job_capabilities_match_bundled_roles_and_job_limits(self):
        import ast
        engine_root = Path(__file__).resolve().parents[1] / 'ustam' / 'engines'
        for provider in ('codex', 'claude', 'opencode'):
            with self.subTest(provider=provider):
                source = ast.parse((engine_root / provider / 'scripts' / 'install.py').read_text())
                name = 'ROLE_FILES' if provider == 'codex' else 'ROLES'
                value = next(node.value for node in source.body if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))
                roles = [ast.literal_eval(key) for key in value.keys] if provider == 'codex' else ast.literal_eval(value)
                expected = {role.replace('_', '-') for role in roles if role != 'owner'}
                capabilities = Runtime.job_capabilities(provider)
                self.assertEqual(set(capabilities['supported_roles']), expected)
                self.assertEqual(capabilities['max_helper_slots'], 50)
                self.assertEqual(capabilities['helper_concurrency'], 1)
                self.assertEqual(capabilities['duplicate_role_policy'], 'first')
                self.assertEqual(capabilities['required_roles'], ['explorer', 'implementer', 'verifier', 'reviewer'])
                execution = set(capabilities['required_roles']) if provider == 'opencode' else expected
                self.assertEqual(set(capabilities['execution_roles']), execution)
        with self.assertRaisesRegex(ValueError, 'Unsupported provider'):
            Runtime.job_capabilities('copilot')

    def test_opencode_plan_reports_only_actual_workers_and_preserves_saved_slots(self):
        payload = self.payload('opencode')
        duplicate = dict(payload['orchestra']['helpers'][1], model='unused-second-implementer')
        payload['orchestra']['helpers'] = [dict(role='advisor', model='saved-advisor', effort='high'),
                                          *payload['orchestra']['helpers'], duplicate]
        job = self.manager.plan(payload)
        self.assertEqual(job['orchestra']['helpers'], payload['orchestra']['helpers'])
        self.assertEqual(job['plan']['configured_helper_count'], 6)
        self.assertEqual(job['plan']['selected_helper_count'], 4)
        self.assertEqual([helper['role'] for helper in job['plan']['execution_helpers']], list(Runtime.REQUIRED_ROLES))
        implementer = next(helper for helper in job['plan']['execution_helpers'] if helper['role'] == 'implementer')
        self.assertEqual(implementer['model'], 'implementer-model')
        self.assertIn('optional roles are not invoked', job['plan']['helper_selection'])
        self.assertEqual(job['plan']['job_capabilities'], Runtime.job_capabilities('opencode'))

    def test_codex_registered_roles_and_fifty_slots_are_job_eligible(self):
        payload = self.payload()
        payload['orchestra']['helpers'] = [dict(role=role, model=role + '-model', effort='high') for role in Runtime.ROLES]
        payload['orchestra']['helpers'] += [dict(payload['orchestra']['helpers'][3]) for _ in range(50 - len(Runtime.ROLES))]
        job = self.manager.plan(payload)
        self.assertEqual(job['plan']['configured_helper_count'], 50)
        self.assertEqual(job['plan']['selected_helper_count'], len(Runtime.ROLES))
        self.assertEqual(len(job['plan']['execution_helpers']), len(Runtime.ROLES))
        payload['orchestra']['helpers'].append(dict(payload['orchestra']['helpers'][3]))
        with self.assertRaisesRegex(ValueError, 'at most 50'):
            self.manager.plan(payload)

    def test_provider_no_alias_and_arbitrary_executable_not_retained(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported provider'):
            self.manager.plan(self.payload('copilot'))
        payload = self.payload()
        payload['orchestra']['chief']['command'] = '/bin/sh'
        self.assertNotIn('command', self.manager.plan(payload)['orchestra']['chief'])
    def test_attention_does_not_invoke_run(self):
        with patch.object(self.manager.runtime, 'prepare', side_effect=RuntimeAttention('Sign in with CLI')):
            job = self.manager.plan(self.payload())
            self.assertEqual(self.manager.start(job['id'])['status'], 'needs_attention')
            self.assertFalse(self.manager.active)

    def test_two_managers_refresh_records_and_preserve_live_owner(self):
        other = JobManager(self.root / 'state', runtime=BlockingRuntime())
        try:
            first = self.manager.plan(self.payload())
            second = other.plan(self.payload())
            self.assertEqual({j['id'] for j in self.manager.list()}, {first['id'], second['id']})
            self.manager.start(first['id'])
            self.assertEqual(other.get(first['id'])['status'], 'running')
            self.assertTrue(other.is_active(str(self.project)))
            with self.assertRaisesRegex(ValueError, 'owning hub'):
                other.cancel(first['id'])
            with self.assertRaisesRegex(ValueError, 'active'):
                other.start(second['id'])
            deadline = time.monotonic() + 1
            while not self.manager.get(first['id'])['session_id'] and time.monotonic() < deadline:
                time.sleep(.01)
            third = JobManager(self.root / 'state', runtime=BlockingRuntime())
            self.assertEqual(third.get(first['id'])['status'], 'running')
            third.close()
            self.manager._event(first['id'], {'type':'session','session_id':'retained-session'})
            other._event(second['id'], {'type':'phase','role':'chief'})
            self.assertEqual(other.get(first['id'])['session_id'], 'retained-session')
            self.assertEqual(len(self.manager.get(second['id'])['events']), 1)
            self.manager.cancel(first['id'])
            self.wait(first['id'])
        finally:
            other.close()

    def test_real_process_plans_and_events_preserve_both_jobs(self):
        second_project = self.root / 'second-project'
        second_project.mkdir()
        code = r'''import json,sys,time
from ustam.jobs import JobManager
class FakeRuntime:
 def prepare(self,job,resume=False):pass
 def run(self,job,cancel,emit,resume=False):
  for index in range(20):
   emit({'type':'session','session_id':'session-'+job['project_id']})
   time.sleep(.01)
  return {'status':'completed','message':'fake CLI completed'}
manager=JobManager(sys.argv[1],runtime=FakeRuntime())
print('ready',flush=True);sys.stdin.readline()
payload=json.loads(sys.argv[2]);job=manager.plan(payload);manager.start(job['id'])
while manager.get(job['id'])['status']=='running':time.sleep(.01)
print(job['id'],flush=True);manager.close()
'''
        payloads = [self.payload(), self.payload()]
        payloads[1]['targets'] = [{'project_id':'second','path':str(second_project)}]
        children = [subprocess.Popen([sys.executable,'-c',code,str(self.root / 'state'),json.dumps(payload)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for payload in payloads]
        try:
            for child in children:
                self.assertEqual(child.stdout.readline().strip(), 'ready')
            for child in children:
                child.stdin.write('go\n');child.stdin.flush()
            identifiers = []
            for child in children:
                output,error=child.communicate(timeout=5)
                self.assertEqual(child.returncode,0,error)
                identifiers.append(output.strip())
            jobs = self.manager.list()
            self.assertEqual({j['id'] for j in jobs},set(identifiers))
            self.assertTrue(all(j['status']=='completed' and len(j['events'])==20 and j['session_id']=='session-'+j['project_id'] for j in jobs))
        finally:
            for child in children:
                if child.poll() is None:child.kill();child.wait()


class RuntimeTests(JobsTests):
    def setUp(self):
        super().setUp()
        self.windows_fixture = None
        self.fixture_popen = None
        if os.name == 'nt':
            # Windows cannot execute Unix shebang fixtures. Use an actual native
            # Python executable and add the private fixture script only in this
            # test harness, leaving production resolver rules and argv intact.
            native_popen = subprocess.Popen
            def fixture_popen(args, *positional, **kwargs):
                values = list(args)
                if (self.windows_fixture is not None and len(values) > 1 and
                    Path(values[0]).resolve() == Path(sys.executable).resolve() and
                    values[1] in ('exec', 'run', 'serve', '-p')):
                    values.insert(1, str(self.windows_fixture))
                return native_popen(values, *positional, **kwargs)
            self.fixture_popen = patch('ustam.runtime.subprocess.Popen', side_effect=fixture_popen)
            self.fixture_popen.start()
    def tearDown(self):
        try:
            super().tearDown()
        finally:
            if self.fixture_popen:
                self.fixture_popen.stop()
    def fake(self, body):
        path = self.root / ('fake-provider.py' if os.name == 'nt' else 'fake-provider')
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o700)
        if os.name == 'nt':
            self.windows_fixture = path
            return str(Path(sys.executable).resolve())
        return str(path)
    def runtime_job(self, body, **limits):
        job = self.manager.plan(self.payload())
        runtime = Runtime(self.root / 'runtime', **limits)
        runtime.directory.mkdir(exist_ok=True)
        runtime.commands[job['id']] = self.fake(body)
        return runtime, job
    def test_actual_subprocess_argv_permissions_and_sanitized_output(self):
        body = "import json\nprint(json.dumps({'type':'thread.started','thread_id':'session-123'}))\nprint(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'status':'completed','summary':'secret-transcript'})}}))\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':7,'output_tokens':3,'unknown':999}}))\n"
        runtime, job = self.runtime_job(body)
        events = []
        result = runtime.run(job, threading.Event(), events.append)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('secret-transcript', json.dumps(events))
        self.assertEqual(events[-1]['usage'], {'input_tokens': 7, 'output_tokens': 3})
        argv = runtime._command(job, False)
        self.assertIn('read-only', argv)
        self.assertFalse(any('dangerously' in value for value in argv))
        roles = list((runtime.directory / job['id']).glob('*.toml'))
        self.assertEqual(len(roles), 4)
        for role in roles:
            text = role.read_text()
            self.assertIn('enabled = false', text)
            self.assertIn('workspace-write' if role.stem == 'implementer' else 'read-only', text)
    def test_deadline_bounded_unterminated_output_and_failure(self):
        for body, limits, phrase in [
            ('import time\ntime.sleep(10)\n', {'max_seconds': .1}, 'Time limit'),
            ("import os\nos.write(1,b'x'*100000)\n", {'max_output': 5000}, 'Output limit'),
            ('import sys\nsys.exit(2)\n', {}, 'attention')]:
            runtime, job = self.runtime_job(body, **limits)
            started = time.monotonic()
            result = runtime.run(job, threading.Event(), lambda e: None)
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(result['status'], 'needs_attention')
            self.assertIn(phrase, result['message'])
    def test_cancel_kills_descendant_process_group(self):
        if os.name == 'nt':
            self.skipTest('POSIX process group assertion')
        marker = self.root / 'descendant-wrote'
        body = 'import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",' + repr('import time;from pathlib import Path;time.sleep(1);Path(' + repr(str(marker)) + ').write_text("bad")') + '])\ntime.sleep(10)\n'
        runtime, job = self.runtime_job(body)
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        self.assertEqual(runtime.run(job, cancel, lambda e: None)['status'], 'cancelled')
        timer.join()
        time.sleep(1.1)
        self.assertFalse(marker.exists())
    def test_project_os_lease(self):
        runtime, job = self.runtime_job('')
        lease = runtime._lease(job['path'])
        try:
            with self.assertRaisesRegex(RuntimeAttention, 'Another hub'):
                runtime._lease(job['path'])
        finally:
            lease.close()
        runtime._lease(job['path']).close()
    def test_claude_chief_only_delegates_worker_tools_restricted(self):
        job = self.manager.plan(self.payload('claude'))
        runtime = Runtime(self.root)
        runtime.commands[job['id']] = '/trusted/claude'
        argv = runtime._command(job, False)
        definitions = json.loads(argv[argv.index('--agents') + 1])
        self.assertEqual(definitions['ustam-chief']['tools'], ['Agent(explorer,implementer,verifier,reviewer)'])
        self.assertIn('Edit', definitions['implementer']['tools'])
        self.assertNotIn('Edit', definitions['reviewer']['tools'])
        self.assertIn('--restricted', argv)
        self.assertEqual(argv[argv.index('--permission-prompts') + 1], 'none')
        self.assertNotIn('Bash', argv[argv.index('--tools') + 1])
    def test_help_capabilities_missing_cli_and_safe_resume(self):
        job = self.manager.plan(self.payload())
        runtime = Runtime(self.root)
        with patch('ustam.runtime.resolve_cli', side_effect=RuntimeAttention('Install codex and sign in with its own CLI')):
            with self.assertRaisesRegex(RuntimeAttention, 'Install codex'):
                runtime.prepare(job)
        cli = self.fake("print('--json --config --sandbox read-only --output-schema --model')\n")
        with patch('ustam.runtime.resolve_cli', return_value=cli):
            runtime.prepare(job)
            runtime.prepare(job, resume=True)
        job['session_id'] = 'session-same'
        argv = runtime._command(job, True)
        self.assertEqual(argv[1:3], ['exec', 'resume'])
        self.assertIn('session-same', argv)
        self.assertIn('sandbox_mode="read-only"', argv)

    def test_opencode_sequential_http_attestation_and_stream(self):
        body = r'''import json,os,sys,threading
if sys.argv[1] == 'serve':
 from http.server import BaseHTTPRequestHandler,HTTPServer
 from socketserver import TCPServer
 import socket
 # A loopback fixture must not depend on runner reverse-DNS configuration.
 def forbid_dns(*args):raise AssertionError('Unexpected DNS lookup in fake server')
 socket.getfqdn=forbid_dns
 class LoopbackServer(HTTPServer):
  def server_bind(self):
   TCPServer.server_bind(self)
   self.server_name='localhost';self.server_port=self.server_address[1]
 config=json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])
 agent=config['agents']['ustam-phase']
 provider,selector=agent['model'].split('/',1);model,variant=selector.split('#',1)
 loaded=dict(agent,id='ustam-phase',model={'providerID':provider,'id':model,'variant':variant})
 class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
   import base64
   assert self.headers['Authorization']=='Basic '+base64.b64encode(('opencode:'+os.environ['OPENCODE_PASSWORD']).encode()).decode()
   self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'data':[loaded]}).encode())
  def log_message(self,*args):pass
 server=LoopbackServer(('127.0.0.1',0),Handler)
 threading.Thread(target=server.serve_forever,daemon=True).start()
 print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port)}),flush=True)
 sys.stdin.read();server.shutdown()
else:
 prompt=sys.argv[-1]
 if 'Return ONLY JSON' in prompt:result={'ownership':['owned.txt'],'brief':'Update only owned.txt'}
 else:result={'status':'completed','summary':'compact evidence'}
 print(json.dumps({'type':'text','part':{'text':json.dumps(result)}}))
'''
        job = self.manager.plan(self.payload('opencode'))
        for item in [job['orchestra']['chief']] + job['plan']['execution_helpers']:
            item['model'] = 'test/' + item['model']
        runtime = Runtime(self.root / 'sequential', max_seconds=20)
        runtime.directory.mkdir()
        runtime.commands[job['id']] = self.fake(body)
        events = []
        result = runtime.run(job, threading.Event(), events.append)
        self.assertEqual(result['status'], 'completed', (result, [e for e in events if e['type'] == 'phase']))
        self.assertEqual([e['role'] for e in events if e['type'] == 'phase'], ['explorer','chief','implementer','verifier','reviewer','chief'])
        self.assertNotIn('OPENCODE_PASSWORD', json.dumps(events))
        phase = dict(job, _role='implementer', _selection=job['plan']['execution_helpers'][1], _ownership=['owned.txt'])
        env = runtime._opencode_environment(phase)
        config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
        rules = config['agents']['ustam-phase']['permissions']
        self.assertIn({'action':'edit','resource':'owned.txt','effect':'allow'}, rules)
        self.assertNotIn({'action':'edit','resource':'*','effect':'allow'}, rules)
        model = config['agents']['ustam-phase']['model']
        self.assertEqual(model, 'test/implementer-model#high')

    def test_opencode_rejects_scope_expansion_and_permission_drift(self):
        runtime = Runtime(self.root)
        job = self.manager.plan(self.payload('opencode'))
        for ownership in (['../outside'], ['/tmp/outside'], ['.codex/config.toml'], ['src/*'], ['..\\outside'], []):
            with self.assertRaises(RuntimeAttention):
                runtime._ownership(job['path'], ownership)
        symlink = self.project / 'linked'
        symlink.symlink_to(self.root)
        with self.assertRaises(RuntimeAttention):
            runtime._ownership(job['path'], ['linked/outside.txt'])
        phase = dict(job, _role='chief', _ownership=[], _selection={'model':'provider/model','effort':'high'})
        agent = {'permissions':[{'action':'*','resource':'*','effect':'allow'}],
                 'model':{'providerID':'provider','id':'model','variant':'high'}}
        with self.assertRaisesRegex(RuntimeAttention, 'exact requested'):
            runtime._verify_opencode_agent(phase, agent)
        agent['permissions'] = runtime._opencode_policy(phase)
        runtime._verify_opencode_agent(phase, agent)
        agent['model']['variant'] = 'low'
        with self.assertRaisesRegex(RuntimeAttention, 'approved orchestra'):
            runtime._verify_opencode_agent(phase, agent)

    def test_opencode_cancel_private_server_start(self):
        job = self.manager.plan(self.payload('opencode'))
        job.update(_role='chief', _ownership=[], _selection={'model':'provider/model','effort':'high'})
        runtime = Runtime(self.root, max_seconds=2)
        runtime.commands[job['id']] = self.fake('import time\ntime.sleep(10)\n')
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(RuntimeAttention, 'did not start'):
            runtime._opencode_server(job, dict(os.environ), cancel)

    def test_provider_error_is_terminal_before_or_after_completion(self):
        completions = {
          'codex': {'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'status':'completed','summary':'done'})}},
          'claude': {'type':'result','structured_output':{'status':'completed','summary':'done'}},
          'opencode': {'type':'text','part':{'text':json.dumps({'status':'completed','summary':'done'})}}}
        errors = {
          'codex': [{'type':'error','message':'approval required secret-never-retain'}, {'type':'turn.failed','error':{'message':'authentication failed secret-never-retain'}}],
          'claude': [{'type':'result','is_error':True,'structured_output':{'status':'completed'},'error':'secret-never-retain'}, {'type':'result','permission_denials':[{'tool':'Edit','input':'secret-never-retain'}]}],
          'opencode': [{'type':'error','error':{'message':'secret-never-retain'}}, {'type':'permission.asked','message':'secret-never-retain'}]}
        for provider in completions:
            for failure in errors[provider]:
                for ordered in ([failure,completions[provider]],[completions[provider],failure]):
                    with self.subTest(provider=provider,order=ordered):
                        body='import os,time\nos.write(1,'+repr(('\n'.join(json.dumps(e) for e in ordered)+'\n').encode())+')\ntime.sleep(10)\n'
                        runtime, job = self.runtime_job(body,max_seconds=3)
                        job['provider']=provider
                        if provider=='opencode':
                            job.update(_role='chief',_selection={'model':'test/chief','effort':'high'},_ownership=[],_prompt='fixture')
                        events=[]
                        started=time.monotonic()
                        with patch.object(runtime,'_opencode_server',return_value=(None,'http://127.0.0.1:12345')):
                            result=runtime._run_one(job,threading.Event(),events.append)
                        self.assertLess(time.monotonic()-started,2)
                        self.assertEqual(result['status'],'needs_attention')
                        self.assertIn('stopped',result['message'])
                        self.assertNotIn('secret-never-retain',json.dumps([result,events]))
                        self.assertEqual(events[-1]['type'],'needs_attention')

    def test_structured_attention_cannot_be_overwritten(self):
        for provider in ('codex','claude','opencode'):
            def event(status):
                response={'status':status,'summary':'secret-never-retain'}
                if provider=='codex':return {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(response)}}
                if provider=='claude':return {'type':'result','structured_output':response}
                return {'type':'text','part':{'text':json.dumps(response)}}
            body='import os\nos.write(1,'+repr(('\n'.join(json.dumps(event(status)) for status in ('needs_attention','completed'))+'\n').encode())+')\n'
            runtime,job=self.runtime_job(body)
            job['provider']=provider
            if provider=='opencode':job.update(_role='chief',_selection={'model':'test/chief','effort':'high'},_ownership=[],_prompt='fixture')
            with patch.object(runtime,'_opencode_server',return_value=(None,'http://127.0.0.1:12345')):
                result=runtime._run_one(job,threading.Event(),lambda e:None)
            self.assertEqual(result['status'],'needs_attention')
            self.assertIn('limitation',result['message'])

    def test_normal_eof_before_process_exit_preserves_success_all_providers(self):
        for provider in ('codex','claude','opencode'):
            response={'status':'completed','summary':'clean final response'}
            if provider=='codex':event={'type':'item.completed','item':{'type':'agent_message','text':json.dumps(response)}}
            elif provider=='claude':event={'type':'result','structured_output':response}
            else:event={'type':'text','part':{'text':json.dumps(response)}}
            body='import os,time\nos.write(1,'+repr((json.dumps(event)+'\n').encode())+')\nos.close(1);os.close(2)\ntime.sleep(.2)\n'
            runtime,job=self.runtime_job(body,max_seconds=2)
            job['provider']=provider
            if provider=='opencode':job.update(_role='chief',_selection={'model':'test/chief','effort':'high'},_ownership=[],_prompt='fixture')
            with patch.object(runtime,'_opencode_server',return_value=(None,'http://127.0.0.1:12345')):
                result=runtime._run_one(job,threading.Event(),lambda event:None)
            self.assertEqual(result['status'],'completed',result)

    def test_eof_shutdown_wait_respects_deadline_cancel_and_own_limit(self):
        event={'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'status':'completed','summary':'done'})}}
        body='import os,time\nos.write(1,'+repr((json.dumps(event)+'\n').encode())+')\nos.close(1);os.close(2)\ntime.sleep(10)\n'
        for seconds,cancel_after,status,phrase in ((.2,None,'needs_attention','Time limit'),(3,.1,'cancelled','Cancelled'),(3,None,'needs_attention','did not exit')):
            runtime,job=self.runtime_job(body,max_seconds=seconds)
            cancel=threading.Event()
            timer=threading.Timer(cancel_after,cancel.set) if cancel_after else None
            if timer:timer.start()
            started=time.monotonic()
            result=runtime.run(job,cancel,lambda event:None)
            self.assertLess(time.monotonic()-started,2.8)
            self.assertEqual(result['status'],status,result)
            self.assertIn(phrase,result['message'])
            if timer:timer.join()

    def test_normal_eof_still_stops_owned_descendants(self):
        if os.name=='nt':self.skipTest('POSIX process group assertion')
        marker=self.root / 'normal-eof-descendant-wrote'
        child='import time;from pathlib import Path;time.sleep(.7);Path('+repr(str(marker))+').write_text("bad")'
        event={'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'status':'completed','summary':'done'})}}
        body='import subprocess,sys,os\nsubprocess.Popen([sys.executable,"-c",'+repr(child)+'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nos.write(1,'+repr((json.dumps(event)+'\n').encode())+')\nos.close(1);os.close(2)\n'
        runtime,job=self.runtime_job(body)
        self.assertEqual(runtime.run(job,threading.Event(),lambda event:None)['status'],'completed')
        time.sleep(.9)
        self.assertFalse(marker.exists())


class CLIResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.bin = self.home / '.local' / 'bin'
        self.bin.mkdir(parents=True)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.patched_home = patch('ustam.runtime.Path.home', return_value=self.home)
        self.patched_home.start()
    def tearDown(self):
        self.patched_home.stop()
        self.temp.cleanup()
    def executable(self, path, source='#!/bin/sh\nprintf "fake CLI\\n"\n'):
        path.write_text(source)
        path.chmod(0o700)
        return path
    @unittest.skipIf(os.name == 'nt', 'POSIX launcher/shebang behavior; native Windows resolver tested separately')
    def test_minimal_desktop_path_resolves_known_installer_directory(self):
        path = self.executable(self.bin / 'claude')
        found = resolve_cli('claude', project=self.project, env={'PATH':'/usr/bin:/bin'})
        self.assertEqual(found,str(path.resolve()))
        with patch('ustam.runtime.Path.cwd', return_value=Path('/')):
            self.assertEqual(resolve_cli('claude', project=self.project, env={'PATH':''}), str(path.resolve()))
        result = subprocess.run([found,'--version'],env=cli_environment(found,project=self.project,env={'PATH':'/usr/bin:/bin'}),capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout.strip(),'fake CLI')
    @unittest.skipIf(os.name == 'nt', 'POSIX launcher/shebang behavior; native Windows resolver tested separately')
    def test_project_and_current_directory_traps_are_never_selected(self):
        trap = self.executable(self.project / 'claude')
        trusted = self.executable(self.bin / 'claude')
        env = {'PATH':str(self.project)+os.pathsep+str(Path.cwd())+os.pathsep+'.'}
        self.assertEqual(resolve_cli('claude',project=self.project,env=env),str(trusted.resolve()))
        cleaned=cli_environment(trusted,project=self.project,env=env)['PATH'].split(os.pathsep)
        self.assertNotIn(str(self.project),cleaned)
        self.assertNotIn(str(Path.cwd()),cleaned)
        self.assertNotIn('.',cleaned)
        trusted.unlink()
        trusted.symlink_to(trap)
        with patch('ustam.runtime._known_cli_bins',return_value=[self.bin]):
            with self.assertRaises(RuntimeAttention):
                resolve_cli('claude',project=self.project,env={'PATH':''})
    def test_windows_batch_shims_refused_and_native_executable_preferred(self):
        for extension in ('.cmd','.bat'):
            path=self.bin / ('codex'+extension)
            path.write_text('@echo unsafe prompt expansion')
            with patch('ustam.runtime._known_cli_bins',return_value=[self.bin]):
                with self.assertRaisesRegex(RuntimeAttention,'batch shim.*native CLI'):
                    resolve_cli('codex',project=self.project,env={'PATH':str(self.bin)},platform='nt')
            path.unlink()
        batch=self.bin / 'codex.cmd';batch.write_text('@echo unsafe')
        native=self.bin / 'codex.exe';native.write_bytes(b'native fixture')
        with patch('ustam.runtime._known_cli_bins',return_value=[self.bin]):
            self.assertEqual(resolve_cli('codex',project=self.project,env={'PATH':str(self.bin)},platform='nt'),str(native.resolve()))
        with self.assertRaises(RuntimeAttention):
            cli_environment(batch,project=self.project,env={},platform='nt')
    @unittest.skipIf(os.name == 'nt', 'POSIX launcher/shebang behavior; native Windows resolver tested separately')
    def test_unix_env_node_wrapper_receives_interpreter_path(self):
        interpreter_bin=self.root / 'node-bin';interpreter_bin.mkdir()
        self.executable(interpreter_bin / 'node','#!/bin/sh\nprintf "node interpreter available\\n"\n')
        wrapper=self.executable(self.bin / 'codex','#!/usr/bin/env node\n')
        with patch('ustam.runtime._known_cli_bins',return_value=[self.bin,interpreter_bin,Path('/usr/bin'),Path('/bin')]):
            env=cli_environment(wrapper,project=self.project,env={'PATH':''})
            self.assertEqual(env['PATH'].split(os.pathsep)[0],str(self.bin.resolve()))
            result=subprocess.run([str(wrapper),'--help'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('node interpreter available',result.stdout)


if __name__ == '__main__':
    unittest.main()
