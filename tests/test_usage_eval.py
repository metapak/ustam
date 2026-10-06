from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USAGE = ROOT / ".codex/tools/usage_report.py"
LOCAL_EVAL = ROOT / ".codex/tools/local_eval.py"


class UsageAndEvalTests(unittest.TestCase):
    def test_selected_project_skips_other_bodies_and_refreshes_memory_cache(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('bounded_usage', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            project = str(sessions / 'project')
            selected = sessions / 'selected.jsonl'
            def metadata(ident, cwd, parent=None):
                return {'type':'session_meta','payload':{'id':ident,'cwd':cwd,'source':{'subagent':{}} if parent else 'cli','parent_thread_id':parent}}
            def token(total):
                return {'type':'token_usage_record','usage':{'total_tokens':total}}
            def write(path, items):
                path.write_text(''.join(json.dumps(item)+'\n' for item in items))
            write(selected, [metadata('root', project), token(12)])
            write(sessions / 'child.jsonl', [metadata('child', project, 'root'), token(7)])
            for index in range(30):
                write(sessions / f'other-{index}.jsonl', [metadata(f'other-{index}', '/different/project'), {'type':'message','prompt':'private body '*1000}, token(999)])
            original = module.json.loads
            with patch.object(module.json, 'loads', wraps=original) as decode:
                report = module.scan(sessions, project=project)
                self.assertEqual(report['totals']['total_tokens'], 19)
                self.assertEqual({a['id'] for a in report['agents']}, {'root', 'child'})
                self.assertFalse(any('private body' in str(call.args[0]) for call in decode.call_args_list))
                decode.reset_mock()
                report['totals']['total_tokens'] = -1
                self.assertEqual(module.scan(sessions, project=project)['totals']['total_tokens'], 19)
                self.assertEqual(decode.call_count, 0)
                with selected.open('a') as stream:
                    stream.write(json.dumps(token(3))+'\n')
                self.assertEqual(module.scan(sessions, project=project)['totals']['total_tokens'], 22)
                write(selected, [metadata('replacement', project), token(2)])
                self.assertEqual(module.scan(sessions, project=project)['totals']['total_tokens'], 9)
                replacement = sessions / 'replacement.tmp'
                write(replacement, [metadata('inode-new', project), token(4)])
                replacement.replace(selected)
                self.assertEqual(module.scan(sessions, project=project)['totals']['total_tokens'], 11)

    def test_project_index_preserves_context_changes_request_overrides_and_appends(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('changing_project_usage', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            project_a, project_b = '/project-a', '/project-b'
            path = sessions / 'changing.jsonl'
            records = [
                {'type':'session_meta','payload':{'id':'root','cwd':project_a,'source':'cli'}},
                {'type':'token_usage_record','usage':{'total_tokens':11}},
                {'type':'turn_context','payload':{'cwd':project_b}},
                {'type':'token_usage_record','usage':{'total_tokens':37}},
                {'type':'turn_context','payload':{'cwd':project_a}},
                {'type':'token_usage_record','payload':{'usage':{'total_tokens':5},'context':{'project':project_b}}},
                {'type':'token_usage_record','cwd':project_b,'usage':{'total_tokens':3}},
            ]
            path.write_text(''.join(json.dumps(item)+'\n' for item in records))
            def compare(expected):
                # The unfiltered accounting path applies all context transitions;
                # select its resulting records as the pre-index semantics oracle.
                baseline = [r for r in module.scan(sessions)['records'] if r['project'] == project_b]
                optimized = module.scan(sessions, project=project_b)
                self.assertEqual(optimized['records'], baseline)
                self.assertEqual(optimized['totals']['total_tokens'], expected)
            compare(45)
            other = sessions / 'previously-unrelated.jsonl'
            other.write_text(json.dumps({'type':'session_meta','payload':{'id':'other','cwd':project_a}})+'\n')
            compare(45)
            with other.open('a') as stream:
                stream.write(json.dumps({'type':'turn_context','payload':{'cwd':project_b}})+'\n')
                stream.write(json.dumps({'type':'token_usage_record','usage':{'total_tokens':19}})+'\n')
            compare(64)

    def test_typed_index_deadline_retains_complete_file_indices_for_one_continuation(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('continuing_index_usage', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            for name, total in (('a', 7), ('b', 11)):
                (sessions / (name+'.jsonl')).write_text(json.dumps({'type':'session_meta','payload':{'id':name,'cwd':'/project'}})+'\n'+json.dumps({'type':'token_usage_record','usage':{'total_tokens':total}})+'\n')
            original = module.project_metadata_lines
            def interrupted(stream, deadline):
                if Path(stream.name).name == 'b.jsonl':
                    raise module.UsageIndexTimeout('fixture index timed out')
                yield from original(stream, deadline)
            with patch.object(module, 'project_metadata_lines', interrupted):
                with self.assertRaises(module.UsageIndexTimeout):
                    module.scan(sessions, project='/project')
            self.assertEqual(len(module._FILE_PROJECTS), 1)
            self.assertIsNone(module._REPORT_CACHE)
            with patch.object(module, 'project_metadata_lines', wraps=original) as index:
                report = module.scan(sessions, project='/project')
                self.assertEqual(index.call_count, 1)
            self.assertEqual(report['totals']['total_tokens'], 18)
            self.assertEqual(report['records_observed'], 2)

    def test_project_index_preserves_cross_file_cumulative_baselines_and_request_precedence(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('shared_thread_usage', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            def write(name, project, stamp, event):
                items = [{'type':'session_meta','payload':{'id':'same-thread','cwd':project}}, {'timestamp':stamp, **event}]
                (sessions/name).write_text(''.join(json.dumps(item)+'\n' for item in items))
            def cumulative(total):
                return {'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}}}}
            write('a.jsonl', '/project-a', '2026-09-01T00:00:00Z', cumulative(100))
            write('b.jsonl', '/project-b', '2026-09-02T00:00:00Z', cumulative(150))
            baseline = [r for r in module.scan(sessions)['records'] if r['project'] == '/project-b']
            report = module.scan(sessions, project='/project-b')
            self.assertEqual(report['records'], baseline)
            self.assertEqual(report['totals'], {'total_tokens':50})
            write('a.jsonl', '/project-a', '2026-09-01T00:00:00Z', {'type':'token_usage_record','usage':{'total_tokens':100}})
            baseline = [r for r in module.scan(sessions)['records'] if r['project'] == '/project-b']
            report = module.scan(sessions, project='/project-b')
            self.assertEqual(report['records'], baseline)
            self.assertEqual(report['records_observed'], 0)
            self.assertEqual(report['totals'], {})

    def test_usage_scan_deadline_fails_without_partial_totals(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('deadline_usage', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            (sessions / 'one.jsonl').write_text('{}\n')
            with patch.object(module, 'SCAN_SECONDS', 0):
                with self.assertRaisesRegex(module.UsageIndexTimeout, 'timed out'):
                    module.scan(sessions, project='/project')
            # Accounting extraction now checkpoints and is eligible for the same bounded continuation.
            with patch.object(module, 'SCAN_SECONDS', 0), patch.object(module, 'project_files', return_value=[(sessions / 'one.jsonl', None)]):
                with self.assertRaisesRegex(ValueError, 'timed out') as raised:
                    module.scan(sessions, project='/project')
                self.assertIsInstance(raised.exception, module.UsageIndexTimeout)

    def test_usage_uses_only_request_token_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            records = [
                {"type": "message", "prompt": "must never appear", "usage": {"total_tokens": 999}},
                {"type": "token_usage_record", "model": "gpt-test", "role": "worker", "thread_id": "t1", "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
                {"type": "token_usage_record", "model": "gpt-test", "role": "worker", "thread_id": "t1", "usage": {"input_tokens": 14, "output_tokens": 5, "total_tokens": 19}},
            ]
            (sessions / "rollout.jsonl").write_text("".join(json.dumps(item) + "\n" for item in records))
            result = subprocess.run([sys.executable, str(USAGE), "--sessions", str(sessions), "--json"], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["totals"]["total_tokens"], 31)
            self.assertEqual(payload["records_observed"], 2)
            self.assertNotIn("must never appear", result.stdout)

    def test_usage_reads_realistic_outer_record_with_nested_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            record = {
                "timestamp": "2026-09-14T00:00:00Z",
                "type": "token_usage_record",
                "payload": {
                    "context": {"model": "gpt-nested", "role": "explorer", "thread_id": "thread-2"},
                    "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
                    "prompt": "private prompt text",
                },
            }
            (sessions / "rollout.jsonl").write_text(json.dumps(record) + "\n")
            result = subprocess.run([sys.executable, str(USAGE), "--sessions", str(sessions), "--json"], text=True, capture_output=True, check=False)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["records_observed"], 1)
            self.assertEqual(payload["totals"]["total_tokens"], 14)
            self.assertEqual((payload["groups"][0]["model"], payload["groups"][0]["role"]), ("gpt-nested", "explorer"))
            self.assertNotIn("private prompt text", result.stdout)

    def test_real_request_shape_dedup_and_filters(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("usage", USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            records = [
                {"type":"session_meta", "payload":{"id":"sanitized-thread", "cwd":"/sample/project", "prompt":"SECRET"}},
                {"type":"turn_context", "payload":{"model":"gpt-test", "role":"implementer"}},
                {"timestamp":"2026-09-01T10:00:00Z", "type":"token_usage_record", "payload":{"usage":{"input_tokens":29000,"cached_input_tokens":20000,"output_tokens":268,"total_tokens":29268},"thread_token_usage":{"total_tokens":29268}}},
                {"timestamp":"2026-09-02T10:00:00Z", "type":"token_usage_record", "payload":{"usage":{"input_tokens":32000,"cached_input_tokens":21000,"output_tokens":426,"total_tokens":32426},"thread_token_usage":{"total_tokens":61694}}},
                {"timestamp":"2026-09-02T10:00:01Z", "type":"event_msg", "payload":{"type":"token_count", "info":{"total_token_usage":{"total_tokens":61694}}}},
            ]
            for filename in ("a.jsonl", "copy.jsonl"):
                (sessions/filename).write_text("".join(json.dumps(r)+"\n" for r in records))
            report = module.scan(sessions)
            self.assertEqual(report["totals"]["total_tokens"], 61694)
            self.assertEqual(report["totals"]["input_tokens"], 61000)
            self.assertEqual(report["records_observed"], 2)
            self.assertEqual(report["duplicates_skipped"], 3)
            self.assertNotIn("SECRET", json.dumps(report))
            filtered = module.scan(sessions, date_from="2026-09-02", project="/sample/project", thread="sanitized-thread")
            self.assertEqual(filtered["totals"]["total_tokens"], 32426)
            self.assertEqual(module.scan(sessions, project="missing")["status"], "unavailable")

    def test_turn_context_model_switch_and_missing_model_are_not_guessed(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('usage_context', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            rows = [
                {'timestamp':'2026-09-01T09:00:00Z','type':'session_meta','payload':{'id':'thread-one','cwd':'/project'}},
                {'timestamp':'2026-09-01T09:01:00Z','type':'turn_context','payload':{'turn_id':'turn-1','model':'gpt-one'}},
                {'timestamp':'2026-09-01T09:02:00Z','type':'token_usage_record','payload':{'turn_id':'turn-1','usage':{'input_tokens':8,'cached_input_tokens':3,'output_tokens':2,'total_tokens':10}}},
                {'timestamp':'2026-09-01T09:02:01Z','type':'event_msg','payload':{'type':'task_complete','turn_id':'turn-1'}},
                {'timestamp':'2026-09-01T09:03:00Z','type':'turn_context','payload':{'turn_id':'turn-2','model_id':'gpt-two'}},
                {'timestamp':'2026-09-01T09:04:00Z','type':'token_usage_record','payload':{'turn_id':'turn-2','usage':{'total_tokens':20}}},
                {'timestamp':'2026-09-01T09:04:01Z','type':'event_msg','payload':{'type':'task_complete','turn_id':'turn-2'}},
                {'timestamp':'2026-09-01T09:05:00Z','type':'turn_context','payload':{'turn_id':'turn-3'}},
                {'timestamp':'2026-09-01T09:06:00Z','type':'token_usage_record','payload':{'turn_id':'turn-3','usage':{'total_tokens':5}}},
            ]
            (sessions/'rollout.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            report = module.scan(sessions)
            self.assertEqual([(row['model'],row['usage']['total_tokens']) for row in report['records']],[('gpt-one',10),('gpt-two',20),('unknown',5)])
            self.assertEqual([(row['turn_start'],row['turn_end']) for row in report['records']],[('2026-09-01T09:01:00Z','2026-09-01T09:02:01Z'),('2026-09-01T09:03:00Z','2026-09-01T09:04:01Z'),('unknown','unknown')])

    def test_legacy_cumulative_resets_and_equal_request_values(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("usage", USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            records = [{"timestamp":f"2026-09-01T10:00:0{i}Z", "type":"event_msg", "payload":{"type":"token_count", "info":{"total_token_usage":{"total_tokens":v}}}} for i,v in enumerate((12,19,4,9))]
            records += [{"type":"token_usage_record", "thread_id":"request-thread", "usage":{"total_tokens":12}}]*2
            (sessions/"a.jsonl").write_text("".join(json.dumps(r)+"\n" for r in records)+"invalid\n")
            report = module.scan(sessions)
            self.assertEqual(report["totals"]["total_tokens"], 52)
            self.assertEqual(report["counter_resets"], 1)
            self.assertEqual(report["malformed_lines_skipped"], 1)

    def test_legacy_cumulative_orders_by_time_across_files_before_filter(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("usage", USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            for filename, stamp, total in (("a.jsonl", "2026-09-02T00:00:00Z", 150), ("b.jsonl", "2026-09-01T00:00:00Z", 100)):
                records = [
                    {"type":"session_meta", "payload":{"id":"same-session"}},
                    {"timestamp":stamp, "type":"event_msg", "payload":{"type":"token_count", "info":{"total_token_usage":{"total_tokens":total}}}},
                ]
                (sessions/filename).write_text("".join(json.dumps(r)+"\n" for r in records))
            report = module.scan(sessions)
            self.assertEqual(report['totals']['total_tokens'], 150)
            self.assertEqual(report['counter_resets'], 0)
            self.assertEqual(module.scan(sessions, date_from='2026-09-02')['totals']['total_tokens'], 50)

    def test_cumulative_orders_parsed_instants_and_keeps_equal_unknown_order(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('instant_usage', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = (
            ('fractional', ('2026-10-01T00:00:00Z', '2026-10-01T00:00:00.100Z')),
            ('offset', ('2026-10-01T01:00:00+02:00', '2026-10-01T00:00:00Z')),
            ('equal', ('2026-10-01T01:00:00+01:00', '2026-10-01T00:00:00Z')),
            ('unknown', ('z-invalid', 'a-invalid')),
        )
        for label, stamps in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                sessions = Path(temporary)
                for filename, stamp, total in zip(('a.jsonl', 'b.jsonl'), stamps, (100, 200)):
                    rows = [{'type':'session_meta','payload':{'id':'same-session'}},
                            {'timestamp':stamp,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}}}}]
                    (sessions/filename).write_text(''.join(json.dumps(row)+'\n' for row in rows))
                report = module.scan(sessions)
                self.assertEqual(report['totals']['total_tokens'], 200)
                self.assertEqual(report['counter_resets'], 0)
                self.assertEqual([row['timestamp'] for row in report['records']], list(stamps))
                self.assertEqual([row['usage']['total_tokens'] for row in report['records']], [100, 100])
                self.assertTrue(all(row['turn_start'] == row['turn_end'] == 'unknown' for row in report['records']))

    def test_cumulative_instant_order_computes_delta_before_date_cutoff(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('cutoff_instant_usage', USAGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary)
            rows = [{'type':'session_meta','payload':{'id':'same-session'}}]
            for stamp, total in (('2026-09-30T23:30:00Z', 100), ('2026-10-01T00:00:00.100Z', 200)):
                rows.append({'timestamp':stamp,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}}}})
            (sessions/'rollout.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            report = module.scan(sessions, date_from='2026-10-01')
            self.assertEqual(report['totals']['total_tokens'], 100)
            self.assertEqual(report['counter_resets'], 0)

    def test_local_eval_requires_argv_and_writes_ignored_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            manifest = repo / "eval.json"
            manifest.write_text(json.dumps({"label": "unit", "argv": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 10}))
            result = subprocess.run([sys.executable, str(LOCAL_EVAL), "--root", str(repo), "--json", str(manifest)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["outcome"], "pass")
            summary = json.loads((repo / ".codex/.bounded-orchestrator/evals/unit.json").read_text())
            self.assertNotIn("sanitized_tail", summary)
            self.assertEqual(len(summary["output_sha256"]), 64)


class DurableIndexTests(unittest.TestCase):
    def module(self, baseline=False):
        import importlib.util
        spec=importlib.util.spec_from_file_location('usage_candidate',ROOT/'ustam/engines/codex/.codex/tools/usage_report.py' if baseline else USAGE)
        result=importlib.util.module_from_spec(spec)
        previous=sys.dont_write_bytecode;sys.dont_write_bytecode=True
        try:spec.loader.exec_module(result)
        finally:sys.dont_write_bytecode=previous
        return result
    def write(self,path,rows):
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    def test_outer_type_body_words_reordered_fields_and_nested_actual_accounting(self):
        from unittest.mock import patch
        module=self.module()
        private='KEEP_PRIVATE_PROMPT '+json.dumps({'type':'token_usage_record','usage':{'total_tokens':99999}})*4096
        bodies=[{'type':'event_msg','payload':{'type':'agent_message','message':private}},{'type':'response_item','payload':{'type':'message','content':private}},{'payload':{'type':'message','content':private},'timestamp':'unknown','type':'response_item'},{'type':'world_state','payload':{'type':'turn_context','text':private}}]
        self.assertEqual(module.outer_type(json.dumps(bodies[2],separators=(', ', ' : '))),'response_item')
        self.assertTrue(module.body_only(json.dumps(bodies[2])))
        records=[{'type':'session_meta','payload':{'id':'thread','cwd':'/selected'}},*bodies,{'type':'response_item','payload':{'type':'token_usage_record','usage':{'total_tokens':7}}},{'type':'response_item','data':{'type':'token_usage_record','usage':{'total_tokens':11}}},{'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':100}}}}]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();self.write(root/'one.jsonl',records)
            decode=module.json.loads
            def checked(line,*args,**kwargs):
                self.assertNotIn('KEEP_PRIVATE_PROMPT',line.decode() if isinstance(line,bytes) else line)
                return decode(line,*args,**kwargs)
            with patch.object(module.json,'loads',checked):candidate=module.scan(root,project='/selected')
            baseline_module=self.module(baseline=True)
            with patch.object(baseline_module,'body_only',return_value=False):baseline=baseline_module.scan(root,project='/selected')
            self.assertEqual(candidate['totals']['total_tokens'],18)
            self.assertEqual(candidate['totals'],baseline['totals'])
            self.assertEqual(candidate['records'],baseline['records'])
    def test_private_complete_cache_reused_after_module_restart_and_invalidates_changes(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=Path(temporary).resolve()/'cache'
            rows=[{'type':'session_meta','payload':{'id':'thread','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}}]
            path=root/'one.jsonl';self.write(path,rows)
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                first=self.module().scan(root,project='/selected')
                module=self.module()
                with patch.object(module,'project_metadata_lines',side_effect=AssertionError('unchanged file re-indexed')):self.assertEqual(module.scan(root,project='/selected')['totals'],first['totals'])
                raw=next(cache.glob('*.json')).read_text();self.assertEqual(set(json.loads(raw)),{'schema','signature','offset','complete','projects','threads'})
                self.assertEqual(cache.stat().st_mode&0o077,0);self.assertEqual(next(cache.glob('*.json')).stat().st_mode&0o077,0)
                with path.open('a') as stream:stream.write(json.dumps({'type':'token_usage_record','usage':{'total_tokens':9}})+'\n')
                self.assertEqual(self.module().scan(root,project='/selected')['totals']['total_tokens'],16)
                self.write(path,[rows[0],{'type':'token_usage_record','usage':{'total_tokens':3}}]);self.assertEqual(self.module().scan(root,project='/selected')['totals']['total_tokens'],3)
                path.unlink();self.write(path,[rows[0],{'type':'token_usage_record','usage':{'total_tokens':5}}]);self.assertEqual(self.module().scan(root,project='/selected')['totals']['total_tokens'],5)
    def test_complete_metadata_append_verifies_prefix_and_reads_only_tail_after_restart(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache'
            path=root/'one.jsonl'
            self.write(path,[{'type':'session_meta','payload':{'id':'shared','cwd':'/other'}},{'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':100}}}}])
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                self.module().scan(root,project='/selected');prefix_size=path.stat().st_size
                with path.open('a') as stream:
                    stream.write(json.dumps({'type':'turn_context','payload':{'cwd':'/selected'}})+'\n')
                    stream.write(json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':150}}}})+'\n')
                module=self.module();starts=[];original=module.project_metadata_lines
                def observe(stream,deadline):
                    starts.append(stream.tell());yield from original(stream,deadline)
                with patch.object(module,'project_metadata_lines',observe):actual=module.scan(root,project='/selected')
                expected=self.module(baseline=True).scan(root,project='/selected')
                self.assertEqual(starts,[prefix_size]);self.assertEqual(actual['records'],expected['records']);self.assertEqual(actual['totals'],{'total_tokens':50})
                # A larger rewrite with the same inode must rebuild ownership.
                self.write(path,[{'type':'session_meta','payload':{'id':'replacement','cwd':'/other'}},{'type':'message','payload':{'text':'private '*200}},{'type':'token_usage_record','usage':{'total_tokens':999}}])
                module=self.module();starts=[];original=module.project_metadata_lines
                with patch.object(module,'project_metadata_lines',observe):actual=module.scan(root,project='/selected')
                self.assertEqual(starts,[0]);self.assertEqual(actual['totals'],{})

    def test_complete_metadata_unterminated_eof_rebuilds_before_append(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache';path=root/'one.jsonl'
            path.write_text(json.dumps({'type':'session_meta','payload':{'id':'thread','cwd':'/other'}}))
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                self.module().scan(root,project='/selected')
                with path.open('a') as stream:
                    stream.write('\n'+json.dumps({'type':'turn_context','payload':{'cwd':'/selected'}})+'\n'+json.dumps({'type':'token_usage_record','usage':{'total_tokens':7}})+'\n')
                module=self.module();starts=[];original=module.project_metadata_lines
                def observe(stream,deadline):
                    starts.append(stream.tell());yield from original(stream,deadline)
                with patch.object(module,'project_metadata_lines',observe):actual=module.scan(root,project='/selected')
                self.assertEqual(starts,[0]);self.assertEqual(actual['totals'],{'total_tokens':7})

    def test_partial_cache_resumes_safe_line_after_restart_without_partial_report(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=Path(temporary).resolve()/'cache'
            rows=[{'type':'session_meta','payload':{'id':'thread','cwd':'/selected'}},{'type':'turn_context','payload':{'cwd':'/other'}},{'type':'token_usage_record','project':'/selected','usage':{'total_tokens':7}}]
            path=root/'one.jsonl';self.write(path,rows)
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                module=self.module();original=module.project_metadata_lines
                def interrupted(stream,deadline):
                    iterator=original(stream,deadline);yield next(iterator);raise module.UsageIndexTimeout('fixture interruption')
                with patch.object(module,'project_metadata_lines',interrupted):
                    with self.assertRaises(module.UsageIndexTimeout):module.scan(root,project='/selected')
                self.assertIsNone(module._REPORT_CACHE);entry=json.loads(next(cache.glob('*.json')).read_text());self.assertFalse(entry['complete']);self.assertGreater(entry['offset'],0)
                restarted=self.module();seen=[];original=restarted.project_metadata_lines
                def resumed(stream,deadline):seen.append(stream.tell());yield from original(stream,deadline)
                with patch.object(restarted,'project_metadata_lines',resumed):result=restarted.scan(root,project='/selected')
                self.assertEqual(seen,[entry['offset']]);self.assertEqual(result['totals']['total_tokens'],7)
    def test_corrupt_symlink_and_midline_cache_safely_bypass(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=Path(temporary).resolve()/'cache'
            path=root/'one.jsonl';self.write(path,[{'type':'session_meta','payload':{'id':'thread','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}}])
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                module=self.module();module.scan(root,project='/selected');destination=next(cache.glob('*.json'));original=json.loads(destination.read_text())
                for raw in ('not-json',json.dumps({**original,'offset':3,'complete':False})):
                    destination.write_text(raw);self.assertEqual(self.module().scan(root,project='/selected')['totals']['total_tokens'],7)
                destination.unlink();outside=Path(temporary).resolve()/'outside';outside.write_text('DO_NOT_CHANGE');destination.symlink_to(outside)
                self.assertEqual(self.module().scan(root,project='/selected')['totals']['total_tokens'],7);self.assertEqual(outside.read_text(),'DO_NOT_CHANGE')

    def test_event_cache_restart_preserves_shared_thread_baselines_and_request_precedence(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache'
            def rows(project,total,stamp):
                return [{'type':'session_meta','timestamp':stamp,'payload':{'id':'same','cwd':project,'source':'cli','instructions':'KEEP_PRIVATE_PROMPT'}},{'type':'event_msg','timestamp':stamp,'payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}}}}]
            self.write(root/'a.jsonl',rows('/other',100,'2026-10-04T00:00:00Z'));self.write(root/'b.jsonl',rows('/selected',150,'2026-10-05T00:00:00Z'))
            baseline=self.module(baseline=True).scan(root,project='/selected',date_from='2026-10-05')
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                candidate=self.module().scan(root,project='/selected',date_from='2026-10-05')
                self.assertEqual(candidate['records'],baseline['records']);self.assertEqual(candidate['agents'],baseline['agents']);self.assertEqual(candidate['totals'],{'total_tokens':50})
                warm=self.module()
                with patch.object(warm,'body_only',side_effect=AssertionError('unchanged source body scanned')):
                    self.assertEqual(warm.scan(root,project='/selected',date_from='2026-10-05')['records'],baseline['records'])
                for entry in (cache/'events').glob('*.json'):
                    text=entry.read_text();self.assertNotIn('KEEP_PRIVATE_PROMPT',text);self.assertNotIn('instructions',text);self.assertEqual(entry.stat().st_mode&0o077,0)
                self.write(root/'a.jsonl',[rows('/other',100,'2026-10-04T00:00:00Z')[0],{'type':'token_usage_record','timestamp':'2026-10-04T00:00:00Z','usage':{'total_tokens':100}}])
                expected=self.module(baseline=True).scan(root,project='/selected');actual=self.module().scan(root,project='/selected')
                self.assertEqual(actual['records'],expected['records']);self.assertEqual(actual['totals'],{})

    def test_accounting_timeout_checkpoint_replays_then_continues_without_partial_success(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache'
            self.write(root/'one.jsonl',[{'type':'session_meta','payload':{'id':'thread','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}},{'type':'token_usage_record','usage':{'total_tokens':9}}])
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                module=self.module();selected=module.project_files(root,'/selected',module.time.monotonic()+30);calls=[];original=module.body_only
                def counted(line):calls.append(1);return original(line)
                with patch.object(module,'project_files',return_value=selected),patch.object(module,'body_only',counted),patch.object(module.time,'monotonic',side_effect=lambda:100 if len(calls)>=2 else 0):
                    with self.assertRaises(module.UsageIndexTimeout):module.scan(root,project='/selected')
                self.assertIsNone(module._REPORT_CACHE);entry=json.loads(next((cache/'events').glob('*.json')).read_text());self.assertFalse(entry['complete']);self.assertEqual(len(entry['events']),2)
                warm=self.module();original=warm.body_only;remaining=[]
                def observed(line):remaining.append(line);return original(line)
                with patch.object(warm,'body_only',observed):report=warm.scan(root,project='/selected')
                self.assertEqual(len(remaining),1);self.assertEqual(report['totals'],{'total_tokens':16});self.assertTrue(json.loads(next((cache/'events').glob('*.json')).read_text())['complete'])

    def test_unterminated_final_record_not_persisted_in_partial_checkpoint(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache';path=root/'one.jsonl'
            path.write_text(json.dumps({'type':'session_meta','payload':{'id':'thread','cwd':'/selected'}})+'\n'+json.dumps({'type':'token_usage_record','usage':{'total_tokens':7}}))
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                module=self.module();selected=module.project_files(root,'/selected',module.time.monotonic()+30);calls=[];original=module.body_only
                def counted(line):calls.append(1);return original(line)
                with patch.object(module,'project_files',return_value=selected),patch.object(module,'body_only',counted),patch.object(module.time,'monotonic',side_effect=lambda:100 if len(calls)>=2 else 0):
                    with self.assertRaises(module.UsageIndexTimeout):module.scan(root,project='/selected')
                entry=json.loads(next((cache/'events').glob('*.json')).read_text());self.assertFalse(entry['complete']);self.assertEqual(len(entry['events']),1)
                actual=self.module().scan(root,project='/selected');baseline=self.module(baseline=True).scan(root,project='/selected')
                self.assertEqual(actual['records'],baseline['records']);self.assertEqual(actual['totals'],{'total_tokens':7});self.assertEqual(len(actual['records']),1)

    def test_growing_file_accounting_checkpoint_keeps_original_range_after_restart(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache';path=root/'one.jsonl'
            self.write(path,[{'type':'session_meta','payload':{'id':'root','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}},{'type':'token_usage_record','usage':{'total_tokens':9}}])
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                module=self.module();selected=module.project_files(root,'/selected',module.time.monotonic()+30);original=module.body_only;calls=[]
                def counted(line):calls.append(1);return original(line)
                with patch.object(module,'project_files',return_value=selected),patch.object(module,'body_only',counted),patch.object(module.time,'monotonic',side_effect=lambda:100 if len(calls)>=2 else 0):
                    with self.assertRaises(module.UsageIndexTimeout):module.scan(root,project='/selected')
                with path.open('a') as output:output.write(json.dumps({'type':'token_usage_record','usage':{'total_tokens':11}})+'\n')
                resumed=self.module().scan(root,project='/selected');self.assertEqual(resumed['totals'],{'total_tokens':16});self.assertEqual(len(resumed['records']),2);self.assertEqual(resumed['deferred_tail_files'],1)
                refreshed=self.module().scan(root,project='/selected');baseline=self.module(baseline=True).scan(root,project='/selected');self.assertEqual(refreshed['records'],baseline['records']);self.assertEqual(refreshed['totals'],{'total_tokens':27})

    def test_partial_eof_row_completed_after_restart_is_counted_once(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache';path=root/'one.jsonl'
            path.write_text(json.dumps({'type':'session_meta','payload':{'id':'root','cwd':'/selected'}})+'\n'+json.dumps({'type':'token_usage_record','usage':{'total_tokens':7}}))
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                module=self.module();selected=module.project_files(root,'/selected',module.time.monotonic()+30);original=module.body_only;calls=[]
                def counted(line):calls.append(1);return original(line)
                with patch.object(module,'project_files',return_value=selected),patch.object(module,'body_only',counted),patch.object(module.time,'monotonic',side_effect=lambda:100 if len(calls)>=2 else 0):
                    with self.assertRaises(module.UsageIndexTimeout):module.scan(root,project='/selected')
                with path.open('a') as output:output.write('\n')
                # A changed unterminated row is rejected before adopting a new range.
                fresh=self.module()
                with self.assertRaises(fresh.UsageIndexTimeout):fresh.scan(root,project='/selected')
                report=self.module().scan(root,project='/selected');self.assertEqual(report['totals'],{'total_tokens':7});self.assertEqual(len(report['records']),1)

    def test_event_cache_corrupt_symlink_and_size_bound_bypass(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache';path=root/'one.jsonl'
            self.write(path,[{'type':'session_meta','payload':{'id':'thread','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}}])
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                self.module().scan(root,project='/selected');entry=next((cache/'events').glob('*.json'));entry.write_text('broken')
                self.assertEqual(self.module().scan(root,project='/selected')['totals'],{'total_tokens':7})
                entry.unlink();outside=root.parent/'outside';outside.write_text('UNCHANGED');entry.symlink_to(outside)
                self.assertEqual(self.module().scan(root,project='/selected')['totals'],{'total_tokens':7});self.assertEqual(outside.read_text(),'UNCHANGED')
                entry.unlink();module=self.module()
                with patch.object(module,'EVENT_CACHE_COUNT',1):self.assertEqual(module.scan(root,project='/selected')['totals'],{'total_tokens':7})
                self.assertFalse(entry.exists())


    def test_growing_selected_and_unrelated_files_use_one_prefix_then_include_tail_next_refresh(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()/'sessions';root.mkdir();cache=root.parent/'cache'
            self.write(root/'a.jsonl',[{'type':'session_meta','payload':{'id':'root','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':37}}])
            self.write(root/'b.jsonl',[{'type':'session_meta','payload':{'id':'other','cwd':'/other'}},{'type':'event_msg','timestamp':'2026-10-04T00:00:00Z','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':100}}}}])
            expected=self.module(baseline=True).scan(root,project='/selected');module=self.module();original=module.project_metadata_lines;appended=set()
            def append_while_reading(stream,deadline):
                for line in original(stream,deadline):
                    if stream.name not in appended:
                        appended.add(stream.name)
                        tail=[{'type':'token_usage_record','usage':{'total_tokens':11}}] if Path(stream.name).name=='a.jsonl' else [{'type':'turn_context','payload':{'cwd':'/selected'}},{'type':'event_msg','timestamp':'2026-10-05T00:00:00Z','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':150}}}}]
                        with Path(stream.name).open('a') as output:
                            for obj in tail:output.write(json.dumps(obj)+'\n')
                    yield line
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}),patch.object(module,'project_metadata_lines',append_while_reading):report=module.scan(root,project='/selected')
            self.assertEqual(report['records'],expected['records']);self.assertEqual(report['totals'],{'total_tokens':37});self.assertEqual(report['deferred_tail_files'],2)
            with patch.dict(os.environ,{'USTAM_USAGE_INDEX_CACHE':str(cache)}):
                refreshed=self.module().scan(root,project='/selected');baseline=self.module(baseline=True).scan(root,project='/selected')
                self.assertEqual(refreshed['records'],baseline['records']);self.assertEqual(refreshed['totals'],{'total_tokens':98})

    def test_empty_captured_prefix_can_grow_without_negative_seek(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();path=root/'empty.jsonl';path.write_bytes(b'');module=self.module();original=module.project_metadata_lines;changed=[]
            def growing(stream,deadline):
                for line in original(stream,deadline):
                    if not changed:
                        changed.append(True);self.write(path,[{'type':'session_meta','payload':{'id':'root','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}}])
                    yield line
            with patch.object(module,'project_metadata_lines',growing):report=module.scan(root,project='/selected')
            self.assertEqual(report['totals'],{});self.assertEqual(report['deferred_tail_files'],1)
            self.assertEqual(module.scan(root,project='/selected')['totals'],{'total_tokens':7})

    def test_growth_rewrite_inode_replacement_and_truncation_fail_closed(self):
        from unittest.mock import patch
        for change in ('rewrite','replace','truncate'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve();path=root/'one.jsonl';self.write(path,[{'type':'session_meta','payload':{'id':'root','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}}]);module=self.module();original=module.project_metadata_lines;changed=[]
                def altered(stream,deadline):
                    for line in original(stream,deadline):
                        if not changed:
                            changed.append(True);old=path.read_text()
                            if change=='replace':path.unlink();path.write_text(old)
                            elif change=='truncate':path.write_text('{}\n')
                            else:path.write_text(old.replace('"total_tokens": 7','"total_tokens": 9')+'{}\n')
                        yield line
                with patch.object(module,'project_metadata_lines',altered):
                    with self.assertRaises(module.UsageIndexTimeout):module.scan(root,project='/selected')
                self.assertIsNone(module._REPORT_CACHE)

    def test_prefix_verification_deadline_resumes_only_without_intervening_mutation(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();path=root/'one.jsonl';self.write(path,[{'type':'session_meta','payload':{'id':'root','cwd':'/selected'}},{'type':'token_usage_record','usage':{'total_tokens':7}}]);module=self.module();original=module.validate_prefix;called=[]
            def timed(path,signature,deadline):
                if not called:
                    called.append(True)
                    with path.open('a') as output:output.write('{}\n')
                    with patch.object(module.time,'monotonic',return_value=100):return original(path,signature,1)
                return original(path,signature,deadline)
            with patch.object(module,'validate_prefix',timed):
                with self.assertRaises(module.UsageIndexTimeout):module.scan(root,project='/selected')
            self.assertTrue(module._PENDING_INDEX);self.assertTrue(module._VERIFY_PROGRESS)
            report=module.scan(root,project='/selected');self.assertEqual(report['totals'],{'total_tokens':7});self.assertFalse(module._PENDING_INDEX)


if __name__ == "__main__":
    unittest.main()
