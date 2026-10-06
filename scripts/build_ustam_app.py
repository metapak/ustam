#!/usr/bin/env python3
"""Build the native Ustam UI launcher and a separate console adapter runtime."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import platform
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verify_assets(root=ROOT):
    manifest = json.loads((root / 'ustam/engine-manifest.json').read_text())
    if manifest.get('schema') != 1 or set(manifest.get('engines', {})) != {'codex', 'claude', 'opencode'}:
        raise ValueError('Invalid engine manifest')
    for provider, record in manifest['engines'].items():
        base = root / 'ustam/engines' / provider
        expected = record['files']
        actual = {p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()}
        if actual != set(expected):
            raise ValueError('Engine asset closure mismatch: ' + provider)
        for relative, digest in expected.items():
            path = base / relative
            if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink():
                raise ValueError('Unsafe engine asset: ' + relative)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Engine asset hash mismatch: ' + relative)
    for name in ('index.html', 'app.js', 'style.css', 'icons.mjs'):
        if not (root / 'ustam/ui' / name).is_file():
            raise ValueError('Missing hub UI asset: ' + name)
    return manifest


def engine_stdlib_modules(root=ROOT):
    """Dynamic backend imports cannot be inferred from the worker entry point."""
    modules = set()
    for path in (root / 'ustam/engines').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for name in names:
                if name and name.partition('.')[0] in sys.stdlib_module_names:
                    modules.add(name)
    return sorted(modules)


def set_bundle_version(bundle, version):
    path = bundle / 'Contents/Info.plist'
    metadata = plistlib.loads(path.read_bytes())
    metadata.update({'CFBundleShortVersionString':version.split('-', 1)[0],
                     'CFBundleVersion':version.split('-', 1)[0],
                     'CFBundleGetInfoString':'Ustam ' + version, 'UstamVersion':version})
    path.write_bytes(plistlib.dumps(metadata))


def archive_package(package, destination):
    # ZipFile follows file symlinks; preserve them because macOS framework
    # symlinks are part of PyInstaller's app bundle structure.
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob('*')):
            relative = path.relative_to(package.parent).as_posix()
            if path.is_symlink():
                info = zipfile.ZipInfo(relative)
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                archive.writestr(info, str(path.readlink()))
            elif path.is_file():
                archive.write(path, relative)


def verify_mac_native_code(bundle):
    """Check frameworks and every Mach-O, including code stored as resources."""
    magic = {bytes.fromhex(value) for value in
             ('feedface', 'cefaedfe', 'feedfacf', 'cffaedfe',
              'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}
    frameworks = {path.resolve() for path in bundle.rglob('*.framework') if path.is_dir()}
    binaries = set()
    for path in bundle.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                if stream.read(4) in magic:
                    binaries.add(path.resolve())
    if not binaries:
        raise ValueError('Mac bundle contains no native code')
    for path in sorted(frameworks | binaries):
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(path)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    return {'frameworks': len(frameworks), 'binaries': len(binaries)}


def seal_mac_bundle(bundle, worker):
    # A console onedir collection can retain a Python framework's Mach-O
    # signature while omitting its resource seal. Restore framework seals
    # inside-out, then sign the outer app after all nested changes are complete.
    frameworks = {path.resolve() for path in worker.rglob('*.framework') if path.is_dir()}
    for framework in sorted(frameworks, key=lambda path: len(path.parts), reverse=True):
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(framework)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(bundle)], check=True)
    return verify_mac_native_code(bundle)


def verify_mac_archive(archive, package_name):
    """Verify the shipped bytes after Apple's extraction, including symlinks."""
    with tempfile.TemporaryDirectory(prefix='ustam-archive-check-') as directory:
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), directory], check=True)
        return verify_mac_native_code(Path(directory) / package_name / 'Ustam.app')


def build(output_dir, rebuild_engines=True):
    if sys.version_info < (3, 11):
        raise RuntimeError('Ustam native build requires Python 3.11 or newer')
    if rebuild_engines:
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_ustam_engines.py')], check=True)
    engines = verify_assets()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = (ROOT / 'ustam/VERSION').read_text().strip()
    if not version or any(character not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for character in version):
        raise ValueError('Invalid native app version')
    system = {'Darwin': 'macos', 'Windows': 'windows', 'Linux': 'linux'}[platform.system()]
    arch = platform.machine().lower()
    arch = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(arch, arch)
    package = output_dir / f'ustam-{version}-{system}-{arch}'
    if package.exists():
        shutil.rmtree(package)
    package.mkdir()
    work = output_dir / f'.ustam-build-{system}-{arch}'
    work.mkdir(exist_ok=True)
    dist = work / 'dist'
    common = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
              '--distpath', str(dist), '--workpath', str(work / 'work'), '--specpath', str(work), '--paths', str(ROOT)]
    data = []
    for name in ('ui', 'engines', 'engine-manifest.json'):
        data += ['--add-data', f'{ROOT / "ustam" / name}:ustam/{name}' if name != 'engine-manifest.json' else f'{ROOT / "ustam" / name}:ustam']
    # The controller receipt hashes its own source; PYZ modules have no source file.
    data += ['--add-data', f'{ROOT / "ustam/protocol.py"}:ustam']
    hidden = [item for module in engine_stdlib_modules() for item in ('--hidden-import', module)]
    subprocess.run([*common, *hidden, '--name', 'UstamWorker', '--console', '--collect-submodules', 'ustam', *data,
                    str(ROOT / 'launchers/ustam_worker.py')], check=True)
    subprocess.run([*common, '--name', 'Ustam', '--windowed', '--osx-bundle-identifier', 'local.ustam.hub',
                    str(ROOT / 'launchers/launch_ustam.py')], check=True)
    if system == 'macos':
        shutil.copytree(dist / 'Ustam.app', package / 'Ustam.app', symlinks=True)
        set_bundle_version(package / 'Ustam.app', version)
        worker = package / 'Ustam.app/Contents/Resources/worker'
    else:
        shutil.copytree(dist / 'Ustam', package, dirs_exist_ok=True, symlinks=True)
        worker = package / 'worker'
    shutil.copytree(dist / 'UstamWorker', worker, symlinks=True)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copy2(ROOT / name, package / name)
    (package / 'START-HERE.txt').write_text('Open Ustam → Select apps → Add projects.\nUstam bundles its runtime; no Python installation is needed.\nInstall and sign in to each selected provider CLI before using it.\nThis build is unsigned; see ustam-hub.md for macOS Gatekeeper limits.\n', encoding='utf-8')
    shutil.copy2(ROOT / 'docs/ustam-hub.md', package / 'ustam-hub.md')
    shutil.copy2(ROOT / 'docs/ustam-hub.tr.md', package / 'ustam-hub.tr.md')
    metadata = {'schema': 1, 'version': version, 'platform': system, 'architecture': arch,
                'signed': False, 'runtime': 'bundled', 'worker': worker.relative_to(package).as_posix(),
                'engines': {key: value['commit'] for key, value in engines['engines'].items()}}
    (package / 'package-manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if system == 'macos':
        # PyInstaller seals nested runtime code. Final plist/worker assembly
        # changes the outer bundle, so seal it last without re-signing children.
        # Ad-hoc sealing provides integrity, not Developer ID trust/notarization.
        seal_mac_bundle(package / 'Ustam.app', worker)
    archive = Path(str(package) + '.zip')
    archive_package(package, archive)
    if system == 'macos':
        verify_mac_archive(archive, package.name)
    metadata.update({'path': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
    (output_dir / (package.name + '.json')).write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    parser.add_argument('--skip-engine-build', action='store_true', help='Use and verify an already generated immutable engine closure')
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir, not args.skip_engine_build), indent=2))

if __name__ == '__main__':
    main()
