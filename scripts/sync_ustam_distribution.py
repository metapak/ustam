#!/usr/bin/env python3
"""Legacy developer-only guarded replication; not used by single-repo releases.

Comparison is the default. --apply is for coordinator-approved source freezes.
Only enumerated generated paths are changed; legacy provider files are preserved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = '.ustam-distribution.json'
FILES = ('engine-sources/antigravity/LICENSE', 'engine-sources/antigravity/NOTICE',
         'engine-sources/antigravity/VERSION', 'engine-sources/antigravity/scripts/install.py',
         'engine-sources/antigravity/scripts/console_settings.py', 'tests/test_antigravity.py',
         'tests/ustam_antigravity_ui.cjs', 'tests/ustam_provider_picker_ui.cjs', 'tests/ustam_install_ui.cjs',
         'docs/antigravity.md', 'docs/antigravity.tr.md', 'ustam/ui/icons.mjs', 'scripts/build_ustam_app.py', 'scripts/build_ustam_engines.py', 'scripts/sync_ustam_distribution.py', 'scripts/install_ustam_macos.py',
         'launchers/Install Ustam.applescript',
         'launchers/launch_ustam.py', 'launchers/ustam_worker.py', '.github/workflows/ustam-app.yml',
         'docs/ustam-hub.md', 'docs/ustam-hub.tr.md',
         'docs/ustam-macos-local-install.md', 'docs/ustam-macos-local-install.tr.md', 'tests/test_ustam_packaging.py',
         'tests/test_ustam_adapters.py', 'tests/test_ustam_hub.py', 'tests/test_ustam_jobs.py',
         'tests/test_ustam_protocol.py', 'tests/test_ustam_model_effort.py', 'tests/test_ustam_codex_bridge.py',
         'tests/ustam_install_fixture.py', 'tests/ustam_claude_cli_fixture.py',
         'tests/ustam_model_effort_ui.cjs', 'tests/ustam_works_ui.cjs',
         'tests/test_ustam_lifecycle.py', 'tests/ustam_notice_locale_ui.cjs',
         'docs/ustam-work-protocol.md', 'docs/ustam-work-protocol.tr.md')

# The immutable import inventory explicitly owns every provider source asset.
_provenance = json.loads((ROOT / 'engine-sources/provenance.json').read_text())
FILES += ('engine-sources/provenance.json',) + tuple(
    record['source_prefix'] + '/' + name
    for record in _provenance['engines'].values() for name in record['files'])
FILES = tuple(sorted(set(FILES)))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(root=None):
    root = root or ROOT
    result = [Path(name) for name in FILES]
    result += [p.relative_to(root) for p in (root / 'ustam').rglob('*')
               if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')]
    return sorted(set(result))


def safe_target(destination, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe generated path')
    target = destination / relative
    if any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(destination)):
        raise ValueError('Generated destination symlink refused: ' + str(relative))
    return target


def sync(destination, apply=False, source_root=None):
    source_root = Path(source_root or ROOT).resolve()
    destination = Path(destination).resolve()
    if destination == source_root or not (destination / '.git').exists():
        raise ValueError('Destination must be a different existing Git checkout')
    manifest_path = safe_target(destination, MANIFEST)
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'schema':1, 'files':{}}
    if previous.get('schema') != 1 or not isinstance(previous.get('files'), dict):
        raise ValueError('Invalid generated manifest')
    for name in previous['files']:
        relative = Path(name)
        if name not in FILES and (not relative.parts or relative.parts[0] != 'ustam'):
            raise ValueError('Manifest contains an unowned path: ' + name)
        safe_target(destination, relative)
    expected = {}
    changes = []
    for relative in source_files(source_root):
        source = source_root / relative
        if not source.is_file() or source.is_symlink():
            raise ValueError('Missing or unsafe canonical file: ' + str(relative))
        name = relative.as_posix()
        expected[name] = digest(source)
        target = safe_target(destination, relative)
        if target.exists():
            if not target.is_file():
                raise ValueError('Generated path is not a file: ' + name)
            current = digest(target)
            if current != expected[name] and current != previous['files'].get(name):
                raise ValueError('Refusing to overwrite an unowned or locally edited file: ' + name)
        if not target.is_file() or digest(target) != expected[name]:
            changes.append(name)
    stale = set(previous['files']) - set(expected)
    for name in stale:
        target = safe_target(destination, name)
        if target.exists() and (not target.is_file() or digest(target) != previous['files'][name]):
            raise ValueError('Refusing to remove locally edited generated file: ' + name)
    # Preflight every path before making any changes.
    if apply:
        for name in changes:
            target = safe_target(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / name, target)
        for name in stale:
            safe_target(destination, name).unlink(missing_ok=True)
        snapshot = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
        manifest_path.write_text(json.dumps({'schema':1, 'snapshot_sha256':snapshot, 'files':expected}, indent=2, sort_keys=True)+'\n')
    return changes + ['remove ' + name for name in sorted(stale)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--apply', action='store_true', help='Apply the frozen generated snapshot after coordinator clearance')
    args = parser.parse_args()
    for relative in sync(args.destination, args.apply):
        print(('copied ' if args.apply else 'differs ') + relative)

if __name__ == '__main__':
    main()
