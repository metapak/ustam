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
            # A timeout after indexing is complete is not eligible for automatic continuation.
            with patch.object(module, 'SCAN_SECONDS', 0), patch.object(module, 'project_files', return_value=[(sessions / 'one.jsonl', None)]):
                with self.assertRaisesRegex(ValueError, 'timed out') as raised:
                    module.scan(sessions, project='/project')
                self.assertNotIsInstance(raised.exception, module.UsageIndexTimeout)

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


if __name__ == "__main__":
    unittest.main()
