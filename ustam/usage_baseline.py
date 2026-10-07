"""Read-only managed-installation evidence; timestamps never come from file mtimes."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.year < 2000 or parsed > datetime.now(timezone.utc):
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        return None


def read_local(root, relative, allow_missing=False):
    path = root
    for part in Path(relative).parts:
        if part in ('..', '/'):
            raise ValueError('Invalid managed receipt path')
        path = path / part
        if path.is_symlink():
            raise ValueError('Unsafe managed receipt')
    if allow_missing and not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Managed receipt unavailable')
    return path.read_bytes()


def owned_manifest(root, value, provider, verify=False):
    if not isinstance(value, dict) or value.get('schema') != 1:
        return False
    files = value.get('files')
    if not isinstance(files, dict) or not files or len(files) > 4096:
        return False
    owned = []
    for name, entry in files.items():
        if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts:
            return False
        if not isinstance(entry, dict):
            return False
        if entry.get('owned') is True or provider == 'opencode' and 'owned' not in entry:
            digest = entry.get('sha256')
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                return False
            owned.append((name, digest))
    if not owned or not any(name.startswith('.'+provider+'/') for name, _ in owned):
        return False
    if verify:
        for name, digest in owned:
            if hashlib.sha256(read_local(root, name)).hexdigest() != digest:
                return False
    return True


def continuous_snapshot(root, current, previous, snapshot, relative):
    entries = snapshot['files']
    if not entries or len(entries) > 4096:
        return False
    allowed = set(current['files']) | set(previous['files']) | {relative, 'AGENTS.md'}
    decoded = {}
    for name, entry in entries.items():
        if name not in allowed or not isinstance(entry, dict) or set(entry) != {'before', 'after'}:
            return False
        live = read_local(root, name, allow_missing=True)
        actual = hashlib.sha256(live).hexdigest() if live is not None else None
        if entry['after'] != actual:
            return False
        before = entry['before']
        if before is not None and not isinstance(before, str):
            return False
        decoded[name] = base64.b64decode(before, validate=True) if before is not None else None
        if decoded[name] is not None and len(decoded[name]) > 2 * 1024 * 1024:
            return False
    # A matching manifest alone cannot establish that its earlier owned files
    # actually existed in the before state of this restorable transaction.
    for name, entry in previous['files'].items():
        if entry.get('owned') is not True:
            continue
        before = decoded[name] if name in decoded else read_local(root, name)
        if before is None or hashlib.sha256(before).hexdigest() != entry['sha256']:
            return False
    return True


def evidence(target, provider):
    root = Path(target)
    relative = f'.{provider}/.bounded-orchestrator/install.json'
    result = {'installed': False, 'manifest_sha': None, 'previous_sha': None, 'started_at': None}
    try:
        raw = read_local(root, relative)
        manifest = json.loads(raw)
        if provider == 'antigravity':
            info = root.stat(follow_symlinks=False)
            if root.is_symlink() or manifest.get('root_identity') != {'device': info.st_dev, 'inode': info.st_ino}:
                return result
        if not owned_manifest(root, manifest, provider, verify=True):
            return result
        result.update(installed=True, manifest_sha=hashlib.sha256(raw).hexdigest())
        stamp = instant(manifest.get('installed_utc' if provider == 'codex' else 'installed_at'))
        if stamp:
            result['started_at'] = stamp.isoformat()
        if provider == 'codex':
            snapshot = json.loads(read_local(root, '.codex/.bounded-orchestrator/console-restore.json'))
            if snapshot.get('schema') != 1 or not isinstance(snapshot.get('files'), dict):
                return result
            entry = snapshot['files'].get(relative)
            if not isinstance(entry, dict) or entry.get('after') != result['manifest_sha'] or not isinstance(entry.get('before'), str):
                return result
            previous_raw = base64.b64decode(entry['before'], validate=True)
            if len(previous_raw) > 2 * 1024 * 1024:
                return result
            previous = json.loads(previous_raw)
            if not owned_manifest(root, previous, provider) or not continuous_snapshot(root, manifest, previous, snapshot, relative):
                return result
            previous_stamp = instant(previous.get('installed_utc'))
            if not previous_stamp or not stamp or previous_stamp > stamp:
                return result
            result.update(previous_sha=hashlib.sha256(previous_raw).hexdigest(), started_at=previous_stamp.isoformat())
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result
