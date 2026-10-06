#!/usr/bin/env python3
"""User-invoked local source installer. Never changes quarantine or project state."""
from __future__ import annotations
import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

IDENTIFIER = 'local.ustam.hub'
PYINSTALLER = '6.22.3'


def trusted_python():
    candidates = [Path('/Library/Frameworks/Python.framework/Versions/Current/bin/python3'),
                  Path('/opt/homebrew/bin/python3'), Path('/usr/local/bin/python3')]
    candidates += sorted(Path('/Library/Frameworks/Python.framework/Versions').glob('3.*/bin/python3'), reverse=True)
    for candidate in candidates:
        if candidate.is_file():
            try:
                result = subprocess.run([str(candidate), '-I', '-c',
                    'import sys; print(int(sys.version_info >= (3, 11)))'], capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and result.stdout.strip() == '1':
                    return candidate
            except (OSError, subprocess.TimeoutExpired):
                pass
    raise RuntimeError('Python 3.11+ required. Install from https://www.python.org/downloads/macos/ and try again.')


def verify_source_assets(source):
    """Validate selected assets with installer-owned stdlib before executing code."""
    def safe(relative):
        path = source / relative
        if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(source)):
            raise ValueError('Source symlink refused: ' + str(relative))
        return path
    safe('scripts/build_ustam_app.py')
    safe('launchers/ustam_worker.py')
    manifest = json.loads(safe('ustam/engine-manifest.json').read_text())
    if manifest.get('schema') != 1 or set(manifest.get('engines', {})) != {'codex', 'claude', 'opencode'}:
        raise ValueError('Invalid engine manifest')
    for provider, record in manifest['engines'].items():
        base = safe('ustam/engines/' + provider)
        expected = record['files']
        actual = set()
        for path in base.rglob('*'):
            if path.is_symlink():
                raise ValueError('Engine symlink refused')
            if path.is_file():
                actual.add(path.relative_to(base).as_posix())
        if actual != set(expected):
            raise ValueError('Engine asset closure mismatch: ' + provider)
        for name, digest in expected.items():
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe engine asset')
            if hashlib.sha256((base / relative).read_bytes()).hexdigest() != digest:
                raise ValueError('Engine asset hash mismatch: ' + name)
    for name in ('index.html', 'app.js', 'style.css', 'icons.mjs'):
        if not safe('ustam/ui/' + name).is_file():
            raise ValueError('Missing hub UI asset: ' + name)


def source_builder(source):
    source = Path(source).resolve()
    path = source / 'scripts/build_ustam_app.py'
    if not path.is_file() or not (source / 'launchers/ustam_worker.py').is_file():
        raise ValueError('Select the extracted Ustam source folder containing scripts, launchers and ustam.')
    verify_source_assets(source)
    spec = importlib.util.spec_from_file_location('ustam_installer_builder', path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.verify_assets(source)
    return builder


def managed_app(path):
    try:
        return plistlib.loads((path / 'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier') == IDENTIFIER
    except (OSError, ValueError):
        return False


def install_bundle(bundle, home, builder, cancelled=lambda: False):
    builder.verify_mac_native_code(bundle)
    applications = Path(home) / 'Applications'
    if applications.is_symlink():
        raise ValueError('Applications symlink refused; nothing was replaced.')
    target = applications / 'Ustam.app'
    if target.is_symlink() or (target.exists() and not managed_app(target)):
        raise ValueError('An unmanaged Ustam.app already exists. Nothing was replaced.')
    applications.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.ustam-install-', dir=applications))
    copied = staging / 'Ustam.app'
    backup = None
    try:
        shutil.copytree(bundle, copied, symlinks=True)
        builder.verify_mac_native_code(copied)
        if cancelled():
            raise InterruptedError('Installation cancelled. Previous installed app was kept.')
        if target.exists():
            backup = applications / ('Ustam-backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + staging.name[-6:] + '.app')
            target.rename(backup)
        try:
            copied.rename(target)
        except BaseException:
            if backup is not None:
                backup.rename(target)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def write_status(job, phase, detail=''):
    temporary = job / 'status.tmp'
    temporary.write_text(json.dumps({'phase': phase, 'detail': detail}), encoding='utf-8')
    temporary.replace(job / 'status.json')


def process_identity(pid):
    # Kernel creation identity survives Python's macOS launcher exec and has
    # finer resolution than ps's one-second lstart text. Read only our PID.
    if sys.platform == 'darwin':
        import ctypes
        class BSDInfo(ctypes.Structure):
            # SDK sys/proc_info.h proc_bsdinfo, MAXCOMLEN=16.
            _fields_ = [('prefix', ctypes.c_uint32 * 12), ('comm', ctypes.c_char * 16),
                        ('name', ctypes.c_char * 32), ('misc', ctypes.c_uint32 * 6),
                        ('seconds', ctypes.c_uint64), ('microseconds', ctypes.c_uint64)]
        info = BSDInfo()
        library = ctypes.CDLL('/usr/lib/libproc.dylib')
        query = library.proc_pidinfo
        query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        query.restype = ctypes.c_int
        if query(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            return None
        if info.prefix[1] == 5:  # SZOMB: dead, awaiting collection
            return None
        return {'started': str(info.seconds) + '.' + str(info.microseconds),
                'group': info.misc[1], 'uid': info.prefix[5]}
    if sys.platform.startswith('linux'):
        try:
            process = Path('/proc') / str(pid)
            fields = (process / 'stat').read_text().rpartition(')')[2].split()
            if fields[0] == 'Z':
                return None
            return {'started': fields[19], 'group': int(fields[2]), 'uid': process.stat().st_uid}
        except (OSError, IndexError, ValueError):
            return None
    raise RuntimeError('Local Mac installer process checks require macOS')


def save_identity(job, name, pid):
    identity = {'pid': pid, 'identity': process_identity(pid)}
    temporary = job / (name + '.tmp')
    temporary.write_text(json.dumps(identity), encoding='utf-8')
    temporary.replace(job / (name + '.json'))


def stop_recorded_stage(job):
    path = job / 'stage-process.json'
    if not path.exists():
        return
    record = json.loads(path.read_text())
    # Match kernel creation time, group and owner before any signal. A stale PID
    # record cannot grant authority to signal an unrelated reused process.
    if not record.get('identity') or process_identity(record['pid']) != record['identity']:
        return
    if record['identity']['group'] != record['pid'] or record['identity']['uid'] != os.getuid():
        return
    for signum in (signal.SIGTERM, signal.SIGKILL):
        if signum == signal.SIGKILL:
            current = process_identity(record['pid'])
            if current is not None and current != record['identity']:
                break
        try:
            os.killpg(record['pid'], signum)
        except (ProcessLookupError, PermissionError):
            break
        if signum == signal.SIGTERM:
            time.sleep(.3)


def job_status(job):
    result = json.loads((job / 'status.json').read_text())
    if result['phase'] in ('done', 'error'):
        return result
    identity_path = job / 'helper-process.json'
    if not identity_path.exists():
        raise RuntimeError('Installer identity is missing. See ' + str(job / 'helper.log'))
    helper = json.loads(identity_path.read_text())
    if not helper.get('identity') or process_identity(helper['pid']) != helper['identity']:
        stop_recorded_stage(job)
        write_status(job, 'error', 'Installer stopped unexpectedly. Previous app was kept during build. See ' + str(job / 'build.log'))
        result = json.loads((job / 'status.json').read_text())
    return result


def stage_members(group):
    # Query only this retained group (also its session on Linux), no global scan.
    result = subprocess.run(['/bin/ps', '-g', str(group), '-o', 'pid='],
                            capture_output=True, text=True, timeout=1)
    members = []
    for value in result.stdout.split():
        pid = int(value)
        identity = process_identity(pid)
        if identity and identity['group'] == group and identity['uid'] == os.getuid():
            members.append(pid)
    return members


def supervised_stage(argv, job, parent):
    # This guardian reserves the group identity until all descendants finish.
    # A caught signal resets to default on child exec, unlike SIG_IGN.
    signal.signal(signal.SIGTERM, lambda *_: None)
    group = os.getpgrp()
    child = subprocess.Popen(argv)
    deadline = time.monotonic() + 3600
    while True:
        parent_gone = process_identity(parent['pid']) != parent['identity']
        if parent_gone or (job / 'cancel').exists() or time.monotonic() > deadline:
            os.killpg(group, signal.SIGTERM)
            time.sleep(.3)
            os.killpg(group, signal.SIGKILL)
        result = child.poll()
        if result is not None and not [pid for pid in stage_members(group) if pid != os.getpid()]:
            return result if result >= 0 else 1
        time.sleep(.1)


def run_stage(argv, job, cwd):
    if (job / 'cancel').exists():
        raise InterruptedError('Installation cancelled. Previous installed app was kept.')
    with (job / 'build.log').open('ab') as log:
        parent = {'pid': os.getpid(), 'identity': process_identity(os.getpid())}
        command = [sys.executable, str(Path(__file__).resolve()), '--stage-job', str(job),
                   '--stage-parent', json.dumps(parent), '--stage', *argv]
        process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        save_identity(job, 'stage-process', process.pid)
        deadline = time.monotonic() + 3600
        try:
            while process.poll() is None:
                if (job / 'cancel').exists():
                    raise InterruptedError('Installation cancelled. Previous installed app was kept.')
                if time.monotonic() > deadline:
                    raise TimeoutError('Build stage exceeded one hour. See build.log.')
                time.sleep(.2)
            if process.returncode:
                raise RuntimeError('Build stage failed. See ' + str(job / 'build.log'))
        finally:
            # Terminate the entire owned group, including compiler descendants.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            # The parent can exit before a descendant which ignored SIGTERM.
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS can return EPERM for an already reaped process group.
                if process.poll() is None:
                    raise
            process.wait(timeout=3)
            (job / 'stage-process.json').unlink(missing_ok=True)



def worker(source, home, job):
    try:
        write_status(job, 'validating', 'Checking source and immutable engine assets')
        builder = source_builder(source)
        python = trusted_python()
        write_status(job, 'environment', 'Creating isolated build environment')
        environment = job / 'venv'
        run_stage([str(python), '-I', '-m', 'venv', str(environment)], job, source)
        runtime = environment / 'bin/python'
        write_status(job, 'dependencies', 'Downloading PyInstaller ' + PYINSTALLER)
        run_stage([str(runtime), '-I', '-m', 'pip', 'install', '--disable-pip-version-check', 'pyinstaller==' + PYINSTALLER], job, source)
        write_status(job, 'building', 'Building and verifying Ustam; this may take several minutes')
        output = job / 'dist'
        run_stage([str(runtime), str(Path(source) / 'scripts/build_ustam_app.py'), '--skip-engine-build', '--output-dir', str(output)], job, source)
        bundles = list(output.glob('ustam-*/Ustam.app'))
        if len(bundles) != 1:
            raise RuntimeError('Build did not produce exactly one Ustam.app')
        if (job / 'cancel').exists():
            raise InterruptedError('Installation cancelled. Previous installed app was kept.')
        write_status(job, 'installing', 'Verifying and installing app; saved Ustam state is preserved')
        installed = install_bundle(bundles[0], home, builder, lambda: (job / 'cancel').exists())
        write_status(job, 'done', str(installed))
    except Exception as error:
        write_status(job, 'error', str(error))


def checked_job(value):
    job = Path(value).resolve()
    if job.parent != Path(tempfile.gettempdir()).resolve() or not job.name.startswith('ustam-local-install-') or not job.is_dir():
        raise ValueError('Invalid installer job folder')
    return job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=Path)
    parser.add_argument('--status')
    parser.add_argument('--cancel')
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--job')
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--stage-job', type=Path)
    parser.add_argument('--stage-parent')
    parser.add_argument('--stage', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.stage is not None:
        return supervised_stage(args.stage, args.stage_job, json.loads(args.stage_parent))
    if args.start:
        source_builder(args.start)
        trusted_python()
        job = Path(tempfile.mkdtemp(prefix='ustam-local-install-'))
        write_status(job, 'starting')
        with (job / 'helper.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(args.start.resolve()),
                '--job', str(job), '--home', str(args.home)], stdout=log, stderr=log, start_new_session=True)
        save_identity(job, 'helper-process', process.pid)
        print(job)
    elif args.worker:
        worker(args.worker.resolve(), args.home, checked_job(args.job))
    elif args.status:
        result = job_status(checked_job(args.status))
        print(result['phase'] + '\n' + result['detail'])
    elif args.cancel:
        (checked_job(args.cancel) / 'cancel').touch()
    else:
        parser.error('Choose --start, --status or --cancel')


if __name__ == '__main__':
    raise SystemExit(main())
