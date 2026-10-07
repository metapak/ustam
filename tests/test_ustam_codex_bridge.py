"""Offline regression for the Codex console's installed manual bridge."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'engine-sources/codex'
sys.dont_write_bytecode = True


def load_source(name, relative):
    spec = importlib.util.spec_from_file_location(name, SOURCE / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodexConsoleBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_source('codex_bridge_installer', 'scripts/install.py')
        cls.catalog = load_source('codex_bridge_catalog', 'scripts/model_catalog.py')
        with mock.patch.dict(sys.modules, {'install': cls.installer, 'model_catalog': cls.catalog}):
            cls.dashboard = load_source('codex_bridge_dashboard', 'scripts/dashboard.py')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        self.console = self.dashboard.Console(self.project, self.base / 'sessions')
        self.console.models = lambda refresh=False: self.catalog.bundled()
        self.relative = '.codex/tools/work_protocol'
        self.wrapper = self.project / self.relative

    def install(self):
        preview = self.console.preview({'preset': 'focused'})
        self.assertTrue(preview['can_save'])
        expected = self.installer.work_protocol_wrapper(self.project).encode()
        self.assertEqual(self.console.pending[2][self.relative], expected)
        self.console.save({'preview_id': preview['preview_id']})
        self.assertEqual(self.wrapper.read_bytes(), expected)
        record = self.installer.load_manifest(self.project)['files'][self.relative]
        self.assertEqual(record['sha256'], hashlib.sha256(expected).hexdigest())
        return expected

    def test_installed_source_bridge_uses_documented_shell_route(self):
        self.install()
        self.assertIn('sh .codex/tools/work_protocol ACTION', (self.project / 'AGENTS.md').read_text())
        state = self.base / 'state'
        state.mkdir()
        (state / 'hub.json').write_text(json.dumps({'projects': [{'id': 'fixture', 'path': str(self.project)}]}))
        def run(*args):
            return subprocess.run(['sh', str(self.wrapper), *args], cwd=self.project,
                                  env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                  capture_output=True, text=True, timeout=15)
        self.assertEqual(run('--help').returncode, 0)
        result = run('list', '--state-dir', str(state))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)['works'], [])
        for action in ('approve', 'apply', 'answer'):
            self.assertNotEqual(run(action).returncode, 0)
        override = run('list', '--project', str(self.base), '--state-dir', str(state))
        self.assertNotEqual(override.returncode, 0)
        self.assertIn('Repeated control option refused', override.stderr)
        self.console.restore({})
        self.assertFalse(self.wrapper.exists())

    def test_frozen_preview_captures_trusted_runtime_and_project(self):
        with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(sys, 'executable', '/trusted runtime/UstamWorker'):
            rendered = self.install().decode()
        self.assertIn("'/trusted runtime/UstamWorker' --work-protocol codex --project", rendered)
        self.assertNotIn('work_protocol.py', rendered)
        self.assertIn(str(self.project), rendered)
        self.console.restore({})
        self.assertFalse(self.wrapper.exists())

    def test_reinstall_and_restore_preserve_user_changes(self):
        original = self.install()
        self.install()
        self.assertEqual(self.wrapper.read_bytes(), original)
        self.console.restore({})
        self.assertEqual(self.wrapper.read_bytes(), original)
        plan = self.console.preview({'preset': 'focused'})
        edited = original + b'# user edit\n'
        self.wrapper.write_bytes(edited)
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            self.console.save({'preview_id': plan['preview_id']})
        self.assertEqual(self.wrapper.read_bytes(), edited)
        plan = self.console.preview({'preset': 'focused'})
        self.assertIn(self.relative, plan['conflicts'])
        self.assertFalse(plan['can_save'])
        self.assertEqual(self.wrapper.read_bytes(), edited)

    def test_restore_refuses_edited_installed_bridge(self):
        original = self.install()
        edited = original + b'# user edit after installation\n'
        self.wrapper.write_bytes(edited)
        with self.assertRaisesRegex(ValueError, 'File changed after Save'):
            self.console.restore({})
        self.assertEqual(self.wrapper.read_bytes(), edited)


if __name__ == '__main__':
    unittest.main()
