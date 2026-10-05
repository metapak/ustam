#!/usr/bin/env python3
"""Read local accounting metadata; never retain message or prompt content."""
from __future__ import annotations
import argparse
import json
import time
import copy
import hashlib
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

COUNTERS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
SCAN_SECONDS = 30
_FILE_PROJECTS = {}
_REPORT_CACHE = None
_FILE_PROGRESS = {}

class UsageIndexTimeout(ValueError):
    """A complete project index is unfinished; no partial report is available."""


def canonical_project(value):
    return str(Path(value).resolve()) if value != 'unknown' and Path(value).is_absolute() else value

def outer_type(line, start=0, field='type', object_type=False):
    """Read the actual root type; quoted payload examples are never type fields."""
    if isinstance(line, bytes):
        line = line.decode('utf-8', errors='replace')
    decoder = json.JSONDecoder()
    whitespace = re.compile(r'\s*')
    delimiters = re.compile(r'["{}\[\]]')
    def space(index):
        return whitespace.match(line, index).end()
    def skip(index):
        index = space(index)
        if index >= len(line): raise ValueError('Incomplete JSON')
        if line[index] not in '{[':
            _, end = decoder.raw_decode(line, index)
            return end
        stack = []
        in_string = False
        while index < len(line):
            if in_string:
                quote = line.find('"', index)
                if quote < 0: raise ValueError('Incomplete JSON string')
                back = quote - 1
                while back >= 0 and line[back] == '\\': back -= 1
                if (quote - back - 1) % 2 == 0: in_string = False
                index = quote + 1
                continue
            match = delimiters.search(line, index)
            if not match: raise ValueError('Incomplete JSON value')
            index = match.start()
            char = line[index]
            if char == '"': in_string = True
            elif char in '{[': stack.append(char)
            elif not stack or (stack.pop(), char) not in (('{','}'), ('[',']')): raise ValueError('Invalid JSON nesting')
            elif not stack: return index + 1
            index += 1
        raise ValueError('Incomplete JSON value')
    try:
        index = space(start)
        if index >= len(line) or line[index] != '{': return None
        index = space(index + 1)
        while index < len(line) and line[index] != '}':
            key, index = decoder.raw_decode(line, index)
            if not isinstance(key, str): return None
            index = space(index)
            if line[index] != ':': return None
            index = space(index + 1)
            if key == field:
                if object_type: return outer_type(line,index)
                value, _ = decoder.raw_decode(line, index)
                return value if isinstance(value, str) else None
            index = space(skip(index))
            if index < len(line) and line[index] == ',': index = space(index + 1)
            elif index >= len(line) or line[index] != '}': return None
        return None
    except (ValueError, IndexError):
        return None


def body_only(line):
    # Envelope names inside escaped prompt/code strings are not event fields.
    prefix = line[:4096] if isinstance(line,bytes) else line
    kind = outer_type(prefix)
    if kind is None and isinstance(line,bytes): kind = outer_type(line)
    if kind not in ('response_item','message','compacted','world_state','event_msg'): return False
    payload_kind = outer_type(prefix,field='payload',object_type=True)
    if payload_kind is None and isinstance(line,bytes):
        payload_kind = outer_type(line,field='payload',object_type=True)
    if payload_kind in ('token_usage_record','token_count','task_complete','turn_aborted'): return False
    if kind == 'event_msg' and payload_kind is None: return False
    envelope_keys = (b'"event"', b'"data"') if isinstance(line, bytes) else ('"event"', '"data"')
    if not any(key in line for key in envelope_keys): return True
    pattern = rb'(?<!\\)"(?:event|data)"\s*:' if isinstance(line,bytes) else r'(?<!\\)"(?:event|data)"\s*:'
    # Conservatively retain real nested event/data envelopes accepted by token_record.
    return re.search(pattern,line) is None


def cache_path(path):
    if not hasattr(os,'getuid'): return None
    directory = os.environ.get('USTAM_USAGE_INDEX_CACHE')
    if not directory:
        return None
    directory = Path(directory)
    if not directory.is_absolute() or any(p.is_symlink() for p in (directory, *directory.parents)):
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
            return None
    except OSError:
        return None
    return directory / (hashlib.sha256(str(path).encode()).hexdigest() + '.json')


def load_progress(path, signature):
    destination = cache_path(path)
    try:
        if destination is None or destination.is_symlink() or not destination.is_file(): return None
        stat = destination.stat()
        if stat.st_size > 262144 or stat.st_uid != os.getuid() or stat.st_mode & 0o077: return None
        entry = json.loads(destination.read_text())
        if set(entry) != {'schema','signature','offset','complete','projects','threads'} or entry['schema'] != 1 or entry['signature'] != list(signature): return None
        offset = entry['offset']
        if type(offset) is not int or not 0 <= offset <= signature[2] or type(entry['complete']) is not bool: return None
        if entry['complete'] and offset != signature[2]: return None
        projects, threads = entry['projects'], entry['threads']
        if not isinstance(projects,list) or not 0 <= len(projects) <= 256 or entry['complete'] and not projects or any(not isinstance(p,str) or len(p)>4096 or p!='unknown' and not Path(p).is_absolute() for p in projects): return None
        if threads is not None and (not isinstance(threads,list) or not 1 <= len(threads) <= 256 or any(not isinstance(t,str) or not 0<len(t)<=256 for t in threads)): return None
        if offset and not entry['complete']:
            with path.open('rb') as stream:
                stream.seek(offset-1)
                if stream.read(1) != b'\n': return None
        return offset, set(projects), set(threads) if threads is not None else None, entry['complete']
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_progress(path, signature, offset, projects, threads, complete=False):
    destination = cache_path(path)
    if destination is None: return
    temporary = None
    try:
        entries = list(destination.parent.glob('*.json'))
        sizes = [(p,p.stat().st_size,p.stat().st_mtime_ns) for p in entries if p != destination]
        total = sum(size for _,size,_ in sizes)
        remaining = len(sizes)
        for old,size,_ in sorted(sizes,key=lambda item:item[2]):
            if remaining < 4096 and total + 262144 <= 32*1024*1024: break
            old.unlink();total -= size;remaining -= 1
        entry = {'schema':1,'signature':list(signature),'offset':offset,'complete':complete,'projects':sorted(projects or {'unknown'} if complete else projects),'threads':sorted(threads) if threads is not None else None}
        raw = json.dumps(entry,separators=(',',':'))
        if len(raw.encode()) > 262144 or destination.is_symlink(): return
        fd, temporary = tempfile.mkstemp(prefix='.index-',dir=destination.parent)
        with os.fdopen(fd,'w') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,destination)
        temporary = None
    except OSError:
        pass
    finally:
        if temporary:
            try: os.unlink(temporary)
            except OSError: pass


def project_metadata_lines(stream, deadline):
    pending = b''
    absolute = stream.tell()
    while True:
        if time.monotonic() >= deadline:
            raise UsageIndexTimeout('Usage history indexing timed out; retry to continue collecting observed counters')
        chunk = stream.read(8 * 1024 * 1024)
        data = pending + chunk
        end = data.rfind(b'\n') + 1 if chunk else len(data)
        pending = data[end:]
        keys = (b'"session_meta"', b'"turn_context"', b'"token_usage_record"')
        positions = [data.find(key, 0, end) for key in keys]
        offset = 0
        while any(position >= 0 for position in positions):
            if time.monotonic() >= deadline:
                raise UsageIndexTimeout('Usage history indexing timed out; retry to continue collecting observed counters')
            position = min(position for position in positions if position >= 0)
            start = max(offset, data.rfind(b'\n', offset, position) + 1)
            line_end = data.find(b'\n', position, end)
            line_end = end if line_end < 0 else line_end + 1
            stream.usage_offset = absolute + line_end
            yield data[start:line_end]
            offset = line_end
            for index, key in enumerate(keys):
                if 0 <= positions[index] < offset:
                    positions[index] = data.find(key, offset, end)
        stream.usage_offset = absolute + end
        yield b''  # Safe progress also advances over chunks with no metadata.
        absolute += end
        if not chunk:
            break

def project_files(root, project, deadline):
    indexed = []
    for path in sorted(root.rglob('*.jsonl')) if root.exists() else []:
        if time.monotonic() >= deadline:
            raise UsageIndexTimeout('Usage history indexing timed out; retry to continue collecting observed counters')
        try:
            stat = path.stat()
            signature = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if project:
                cached = _FILE_PROJECTS.get(str(path))
                disk = load_progress(path, signature) if cached is None or cached[0] != signature else None
                if disk and disk[3]:
                    cached = (signature, disk[1], disk[2])
                    _FILE_PROJECTS[str(path)] = cached
                if cached is None or cached[0] != signature:
                    progress = _FILE_PROGRESS.get(str(path))
                    if progress and progress[0] == signature:
                        offset, projects, threads = progress[1], set(progress[2]), set(progress[3]) if progress[3] is not None else None
                    elif disk:
                        offset, projects, threads = disk[:3]
                    else:
                        offset, projects, threads = 0, set(), {path.stem}
                    with path.open('rb') as stream:
                        stream.seek(offset)
                        try:
                            for line in project_metadata_lines(stream, deadline):
                                if line and not body_only(line):
                                    try:
                                        obj = json.loads(line)
                                    except (json.JSONDecodeError, UnicodeError):
                                        projects.add('unknown')
                                        obj = None
                                    if isinstance(obj, dict):
                                        payload = obj.get('payload', {})
                                        sources = []
                                        if obj.get('type') in ('session_meta', 'turn_context') and isinstance(payload, dict):
                                            sources.append(payload)
                                            if obj.get('type') == 'session_meta' and isinstance(payload.get('id'), str):
                                                threads.add(payload['id'])
                                        record = token_record(obj)
                                        if record is not None:
                                            sources.append(record)
                                        for source in sources:
                                            for key in ('thread_id', 'session_id', 'conversation_id'):
                                                value = source.get(key)
                                                if isinstance(value, (str, int)) and str(value):
                                                    threads.add(str(value))
                                            for key in ('cwd', 'project'):
                                                value = source.get(key)
                                                if isinstance(value, (str, int)) and str(value) and str(value) not in projects:
                                                    projects.add(canonical_project(str(value)))
                                offset = getattr(stream, 'usage_offset', offset)
                                if len(projects) > 256 or threads is None or len(threads) > 256:
                                    projects, threads = {'unknown'}, None
                                    break
                                if len(_FILE_PROGRESS) >= 4096: _FILE_PROGRESS.clear()
                                _FILE_PROGRESS[str(path)] = (signature, offset, set(projects), set(threads))
                        except UsageIndexTimeout:
                            save_progress(path, signature, offset, projects, threads)
                            raise
                    after = path.stat()
                    if (str(path), after.st_ino, after.st_size, after.st_mtime_ns) != signature:
                        _FILE_PROGRESS.pop(str(path), None)
                        raise UsageIndexTimeout('Usage history changed during indexing; retry with the updated records')
                    cached = (signature, projects or {'unknown'}, threads)
                    if len(_FILE_PROJECTS) >= 4096:
                        _FILE_PROJECTS.clear()
                    _FILE_PROJECTS[str(path)] = cached
                    _FILE_PROGRESS.pop(str(path), None)
                    save_progress(path, signature, stat.st_size, cached[1], cached[2], complete=True)
                indexed.append((path, signature, cached[1], cached[2]))
            else:
                indexed.append((path, signature, {'unknown'}, {path.stem}))
        except OSError:
            # Preserve unreadable-file accounting in scan.
            indexed.append((path, None, {'unknown'}, None))
    # Cumulative baselines and request-format precedence are per thread, before
    # project filtering. Include every file sharing an eligible thread, even if
    # that file's records belong to another project. Multi-thread files can link
    # further dependencies, so compute the closure before accounting.
    if not project or any(threads is None for _, _, _, threads in indexed):
        return [(path, signature) for path, signature, _, _ in indexed]
    eligible = {index for index, (_, _, projects, _) in enumerate(indexed) if 'unknown' in projects or project in projects}
    threads = set().union(*(indexed[index][3] for index in eligible))
    while True:
        linked = {index for index, (_, _, _, identities) in enumerate(indexed) if identities & threads}
        new = linked - eligible
        if not new:
            break
        eligible.update(new)
        threads.update(*(indexed[index][3] for index in new))
    return [(path, signature) for index, (path, signature, _, _) in enumerate(indexed) if index in eligible]

META = ('model', 'model_id', 'effort', 'reasoning_effort', 'model_reasoning_effort', 'role', 'agent_role', 'thread_id', 'session_id', 'conversation_id', 'cwd', 'project')

def scalar(obj, *keys):
    return next((str(obj[k]) for k in keys if isinstance(obj.get(k), (str, int)) and str(obj[k])), 'unknown')

def token_record(obj):
    payload = obj.get('payload', {})
    candidates = [obj] + [obj[k] for k in ('payload', 'event', 'data') if isinstance(obj.get(k), dict)]
    for item in candidates:
        if item.get('type', obj.get('type')) == 'token_usage_record' and isinstance(item.get('usage'), dict):
            result = {k: v for source in (obj, item, item.get('context', {})) if isinstance(source, dict) for k, v in source.items() if k in META and isinstance(v, (str, int))}
            result.update(usage=item['usage'], semantics='request', timestamp=scalar(obj, 'timestamp'), event_id=scalar(item, 'request_id', 'id'), turn_id=scalar(item, 'turn_id'))
            return result
    # Older CLI event_msg/token_count records are cumulative. Prefer total to last
    # so repeated last-token snapshots do not become new requests.
    if isinstance(payload, dict) and payload.get('type') == 'token_count':
        info = payload.get('info')
        if isinstance(info, dict) and isinstance(info.get('total_token_usage'), dict):
            return {'usage': info['total_token_usage'], 'semantics': 'cumulative', 'timestamp': scalar(obj, 'timestamp'), 'event_id': 'unknown'}
    return None

def instant(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except ValueError:
        return None

def scan(root: Path, *, date_from='', date_to='', project='', thread=''):
    global _REPORT_CACHE
    project = canonical_project(project)
    deadline = time.monotonic() + SCAN_SECONDS
    selected = project_files(root, project, deadline)
    cache_key = (str(root.resolve()), date_from, date_to, project, thread, tuple(signature for _, signature in selected))
    if project and _REPORT_CACHE is not None and _REPORT_CACHE[0] == cache_key:
        return copy.deepcopy(_REPORT_CACHE[1])
    groups = defaultdict(lambda: defaultdict(int))
    records, raw_records, seen, previous = [], [], set(), {}
    turns, agents = {}, {}
    files = malformed = duplicates = resets = unreadable = 0
    for path, _ in selected:
        if time.monotonic() >= deadline:
            raise ValueError('Usage collection timed out; retry to collect observed counters')
        files += 1
        metadata = {'thread_id': path.stem}
        try:
            lines = path.open(encoding='utf-8', errors='replace')
        except OSError:
            unreadable += 1
            continue
        with lines:
            for index, line in enumerate(lines):
                if time.monotonic() >= deadline:
                    raise ValueError('Usage collection timed out; retry to collect observed counters')
                # Message/tool bodies are not accounting data. Avoid decoding large
                # unrelated payloads; only metadata and accounting events are needed.
                if body_only(line):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get('payload', {})
                if obj.get('type') in ('session_meta', 'turn_context') and isinstance(payload, dict):
                    if obj['type'] == 'turn_context':
                        metadata.pop('model', None)
                        metadata.pop('model_id', None)
                        for key in ('effort', 'reasoning_effort', 'model_reasoning_effort'):
                            metadata.pop(key, None)
                    metadata.update({k: v for k, v in payload.items() if k in META and isinstance(v, (str, int))})
                    if obj['type'] == 'session_meta' and isinstance(payload.get('id'), str):
                        metadata['thread_id'] = payload['id']
                        agent_id = payload['id']
                        parent = payload.get('parent_thread_id') if isinstance(payload.get('parent_thread_id'), str) else None
                        source = 'subagent' if isinstance(payload.get('source'), dict) and 'subagent' in payload['source'] else 'root' if isinstance(payload.get('source'), str) else 'unknown'
                        name = payload.get('agent_nickname') if isinstance(payload.get('agent_nickname'), str) else ''
                        role = payload.get('agent_role') if isinstance(payload.get('agent_role'), str) else ''
                        candidate = {'id': agent_id, 'parent': parent, 'source': source, 'name': name[:80], 'role': role[:80], 'project': canonical_project(scalar(payload, 'cwd')), 'observed_at': scalar(obj, 'timestamp')}
                        previous_agent = agents.get(agent_id)
                        if previous_agent and any(previous_agent.get(k) != candidate[k] for k in ('parent', 'source', 'name', 'role', 'project')):
                            candidate['ambiguous'] = True
                        if previous_agent and previous_agent.get('ambiguous'):
                            candidate['ambiguous'] = True
                        agents[agent_id] = candidate
                    if obj['type'] == 'turn_context' and isinstance(payload.get('turn_id'), str):
                        started = scalar(obj, 'timestamp')
                        when = instant(started)
                        if when is not None:
                            key = (scalar(metadata, 'thread_id', 'session_id', 'conversation_id'), payload['turn_id'])
                            turn = turns.setdefault(key, {})
                            if 'start' in turn and turn['start'][0] != when:
                                turn['ambiguous'] = True
                            turn['start'] = (when, started)
                event_stamp = scalar(obj, 'timestamp')
                event_time = instant(event_stamp)
                if event_time is not None:
                    event_tid = scalar(metadata, 'thread_id', 'session_id', 'conversation_id')
                    terminal = obj.get('type') == 'event_msg' and isinstance(payload, dict) and payload.get('type') in ('task_complete', 'turn_aborted')
                    if terminal and isinstance(payload.get('turn_id'), str):
                        key = (event_tid, payload['turn_id'])
                        turn = turns.setdefault(key, {})
                        if 'end' in turn and turn['end'][0] != event_time:
                            turn['ambiguous'] = True
                        turn['end'] = (event_time, event_stamp)
                record = token_record(obj)
                if record is None:
                    continue
                meta = {**metadata, **record}
                model = scalar(meta, 'model', 'model_id')
                effort = scalar(meta, 'effort', 'reasoning_effort', 'model_reasoning_effort')
                role = scalar(meta, 'role', 'agent_role')
                tid = scalar(meta, 'thread_id', 'session_id', 'conversation_id')
                proj = canonical_project(scalar(meta, 'project', 'cwd'))
                stamp = record['timestamp']
                current = {k: v for k, v in record['usage'].items() if k in COUNTERS and type(v) is int and v >= 0}
                if not current:
                    continue
                raw_records.append({'model': model, 'effort': effort, 'role': role, 'thread': tid, 'session_id': scalar(meta, 'session_id'), 'project': proj, 'timestamp': stamp, 'semantics': record['semantics'], 'event_id': record['event_id'], 'turn_id': record.get('turn_id', 'unknown'), 'usage': current})
    # Sort accounting events before deltas and date filters. Rollout filenames
    # are not a chronological contract, including duplicate/exported logs.
    for record in raw_records:
        turn = turns.get((record['thread'], record['turn_id']))
        if turn is not None:
            when = instant(record['timestamp'])
            if when is None or 'start' not in turn or 'end' not in turn or not turn['start'][0] <= when <= turn['end'][0]:
                turn['ambiguous'] = True
    for record in raw_records:
        turn = turns.get((record['thread'], record['turn_id']), {})
        when = instant(record['timestamp'])
        bounded = not turn.get('ambiguous') and 'start' in turn and 'end' in turn and when is not None and turn['start'][0] <= when <= turn['end'][0]
        record['turn_start'] = turn['start'][1] if bounded else 'unknown'
        record['turn_end'] = turn['end'][1] if bounded else 'unknown'
    def accounting_order(record):
        when = instant(record['timestamp'])
        # Python's stable sort preserves file/record order for equivalent instants
        # and undated events, which cannot establish a chronological position.
        return record['thread'], when is None, when if when is not None else 0
    raw_records.sort(key=accounting_order)
    request_threads = {r['thread'] for r in raw_records if r['semantics'] == 'request'}
    for record in raw_records:
        tid, stamp, current = record['thread'], record['timestamp'], record['usage']
        identity = (tid, record['turn_id'], record['semantics'], record['event_id'], stamp, tuple(sorted(current.items())))
        if (stamp != 'unknown' or record['event_id'] != 'unknown') and identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        if record['semantics'] == 'cumulative' and tid in request_threads:
            continue
        delta = current
        if record['semantics'] == 'cumulative':
            before = previous.get(tid, {})
            reset = any(v < before.get(k, 0) for k, v in current.items())
            resets += int(reset)
            delta = current if reset else {k: v - before.get(k, 0) for k, v in current.items()}
            previous[tid] = current
        day = stamp[:10] if stamp != 'unknown' else ''
        if (date_from and (not day or day < date_from)) or (date_to and (not day or day > date_to)) or (project and record['project'] != project) or (thread and tid != thread):
            continue
        records.append({k: v for k,v in record.items() if k not in ('event_id', 'usage', 'turn_id')} | {'usage': delta})
    for record in records:
        key = tuple(record[k] for k in ('model', 'role', 'thread', 'project'))
        for name, value in record['usage'].items():
            groups[key][name] += value
    result_groups, grand = [], defaultdict(int)
    for key, usage in sorted(groups.items()):
        result_groups.append(dict(zip(('model', 'role', 'thread', 'project'), key), usage=dict(usage)))
        for name, value in usage.items():
            grand[name] += value
    report = {'platform': 'codex', 'status': 'available' if records else 'unavailable', 'source': 'local session request usage; legacy cumulative token_count fallback', 'files_scanned': files, 'records_observed': len(records), 'duplicates_skipped': duplicates, 'counter_resets': resets, 'unreadable_files': unreadable, 'malformed_lines_skipped': malformed, 'totals': dict(grand), 'groups': result_groups, 'records': records, 'agents': list(agents.values()), 'cost': None, 'limitations': 'Observed counters only, not quota or billing. Cache is included in input; reasoning is included in output. Unknown metadata is unavailable; thread filename is a fallback. Unknown legacy timestamps cannot be reliably ordered. Legacy resets count a new segment. Request records take precedence per thread; mixed-format logs may be incomplete. No reliable pricing metadata; cost unavailable.'}

    if project and len(records) <= 10000 and len(agents) <= 10000 and all(signature is not None for _, signature in selected):
        _REPORT_CACHE = (cache_key, copy.deepcopy(report))
    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions', type=Path, default=Path.home()/'.codex/sessions')
    parser.add_argument('--json', action='store_true')
    for key in ('date-from', 'date-to', 'project', 'thread'):
        parser.add_argument('--'+key, default='')
    args = parser.parse_args(argv)
    report = scan(args.sessions.expanduser(), date_from=args.date_from, date_to=args.date_to, project=args.project, thread=args.thread)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else f"Codex local usage: {report['status']}\n{report['totals']}\n{report['limitations']}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
