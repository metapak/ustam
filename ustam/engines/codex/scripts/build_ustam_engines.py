#!/usr/bin/env python3
"""Build trusted runtime closures from immutable Git objects, never working trees."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PINS = {'codex': ('codex-bounded-orchestrator', '25d2f993f324982f495cc41befd16202293a47b9'),
        'claude': ('claude-bounded-orchestrator', '3fbef57dbf3e075e6256db31d407db4cf1cf9260'),
        'opencode': ('opencode-bounded-orchestrator', '564b4701142142d1ca6dc97ef2ef910450384e56')}
PREFIXES = ('.agents/', '.codex/', '.claude/', '.opencode/', 'scripts/', 'templates/', 'presets/')
TOP = {'VERSION', 'LICENSE', 'NOTICE'}

def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])

def build():
    manifest = {'schema': 1, 'closure': 'All tracked runtime/instruction/template/preset/script trees; tests, docs, launchers and setup wrappers are not read by backend APIs.', 'engines': {}}
    for provider, (name, pin) in PINS.items():
        repo = ROOT.parent / name
        files = {}
        for entry in git(repo, 'ls-tree', '-rz', '--full-tree', pin).split(b'\0'):
            if not entry:
                continue
            metadata, encoded = entry.split(b'\t', 1)
            relative = encoded.decode('utf-8')
            if relative not in TOP and not relative.startswith(PREFIXES):
                continue
            if metadata.split()[0] not in (b'100644', b'100755'):
                raise ValueError('Nonregular engine asset: ' + relative)
            data = git(repo, 'show', pin + ':' + relative)
            destination = ROOT / 'ustam/engines' / provider / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            files[relative] = hashlib.sha256(data).hexdigest()
        manifest['engines'][provider] = {'commit': pin, 'files': files}
    (ROOT / 'ustam/engine-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')

if __name__ == '__main__':
    build()
