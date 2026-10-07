#!/usr/bin/env python3
"""Build trusted runtime closures from immutable Git objects, never working trees."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PINS = {'codex': ('codex-bounded-orchestrator', '46ca3064b7665ebe683b77b496fe5d1fe218bdbc'),
        'claude': ('claude-bounded-orchestrator', 'aeefd8551d106c9b43e5fc6490fdfee1047aecbc'),
        'opencode': ('opencode-bounded-orchestrator', 'fa8068f6bc360b6c765570d87d4cf39ffa597f32'),
        'antigravity': ('codex-bounded-orchestrator', '80613c6c7c11d2a3398b3499a402f328575f0fe0', 'engine-sources/antigravity')}
PREFIXES = ('.agents/', '.codex/', '.claude/', '.opencode/', '.antigravity/', 'scripts/', 'templates/', 'presets/')
TOP = {'VERSION', 'LICENSE', 'NOTICE'}

def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])

def frozen_files(repo, pin, source_prefix=''):
    """Read a runtime-only subtree from an immutable object, never disk source."""
    if len(pin) != 40 or any(c not in '0123456789abcdef' for c in pin):
        raise ValueError('Engine pin must be a full immutable commit ID')
    prefix = source_prefix.rstrip('/')
    if Path(source_prefix).is_absolute() or (prefix and any(p in ('', '.', '..') for p in prefix.split('/'))):
        raise ValueError('Unsafe engine source prefix')
    prefix = prefix + '/' if prefix else ''
    files = {}
    for entry in git(repo, 'ls-tree', '-rz', '--full-tree', pin).split(b'\0'):
        if not entry:
            continue
        metadata, encoded = entry.split(b'\t', 1)
        source_name = encoded.decode('utf-8')
        if not source_name.startswith(prefix):
            continue
        relative = source_name[len(prefix):]
        if relative not in TOP and not relative.startswith(PREFIXES):
            continue
        if metadata.split()[0] not in (b'100644', b'100755'):
            raise ValueError('Nonregular engine asset: ' + relative)
        files[relative] = git(repo, 'show', pin + ':' + source_name)
    if not files:
        raise ValueError('Empty immutable engine source closure')
    return files

def build():
    manifest = {'schema': 1, 'closure': 'All tracked runtime/instruction/template/preset/script trees; tests, docs, launchers and setup wrappers are not read by backend APIs.', 'engines': {}}
    for provider, source in PINS.items():
        name, pin, *prefix = source
        source_prefix = prefix[0] if prefix else ''
        repo = ROOT.parent / name
        files = {}
        for relative, data in frozen_files(repo, pin, source_prefix).items():
            destination = ROOT / 'ustam/engines' / provider / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            files[relative] = hashlib.sha256(data).hexdigest()
        manifest['engines'][provider] = {'commit': pin, 'files': files}
        if source_prefix:
            manifest['engines'][provider].update({'source_repository': name, 'source_prefix': source_prefix})
    (ROOT / 'ustam/engine-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')

if __name__ == '__main__':
    build()
