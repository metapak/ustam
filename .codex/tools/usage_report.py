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
    """Complete index/event collection is unfinished; no partial report is available."""


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
        if set(entry) != {'schema','signature','offset','complete','projects','threads'} or entry['schema'] != 1 or entry['signature'][:5] != list(signature[:5]): return None
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
        return offset, set(projects), set(threads) if threads is not None else None, entry['complete'],tuple(entry['signature'])
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


_VERIFY_PROGRESS = {}
_PENDING_INDEX = {}
_DEFERRED_TAIL = set()


def file_signature(path, stat):
    return (str(path),stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_dev)


def same_identity(signature, stat):
    return stat.st_ino == signature[1] and (len(signature)<5 or stat.st_dev == signature[4])


class PrefixStream:
    """Hash exactly one captured byte range while parsing it; never follow a tail."""
    def __init__(self, stream, signature, deadline):
        self.stream,self.signature,self.deadline = stream,signature,deadline
        self.bound = signature[2]
        self.hash = hashlib.sha256()
        self.usage_offset = 0
        opened = os.fstat(stream.fileno())
        if not same_identity(signature,opened) or opened.st_size < self.bound:
            raise UsageIndexTimeout('Usage history identity changed; retry')
    def __getattr__(self,name): return getattr(self.stream,name)
    def tell(self): return self.stream.tell()
    def read(self, size):
        data = self.stream.read(min(size,max(0,self.bound-self.tell())))
        self.last_start=self.tell()-len(data)
        self.before_last=self.hash.copy();self.last_chunk=data
        self.hash.update(data)
        return data
    def readline(self):
        data = self.stream.readline(max(0,self.bound-self.tell())) if self.tell()<self.bound else b''
        self.last_start=self.tell()-len(data)
        self.before_last=self.hash.copy();self.last_chunk=data
        self.hash.update(data)
        return data
    def seek(self, offset):
        self.stream.seek(0);self.hash = hashlib.sha256()
        while self.tell()<offset:
            if time.monotonic()>=self.deadline:
                raise UsageIndexTimeout('Usage prefix checkpoint validation timed out; retry')
            self.read(min(8*1024*1024,offset-self.tell()))
        return offset
    def checkpoint_digest(self,offset):
        if offset==0:return hashlib.sha256().hexdigest()
        if not hasattr(self,'last_start') or offset<self.last_start:return None
        digest=self.before_last.copy();digest.update(self.last_chunk[:offset-self.last_start]);return digest.hexdigest()
    def complete_digest(self):
        while self.tell()<self.bound:
            if time.monotonic()>=self.deadline:
                raise UsageIndexTimeout('Usage prefix hashing timed out; retry')
            if not self.read(8*1024*1024): raise UsageIndexTimeout('Usage history truncated; retry')
        return self.hash.hexdigest()


def validate_prefix(path, signature, deadline):
    """Exact-stat fast path, otherwise cryptographically prove the captured prefix."""
    current = path.stat()
    if not same_identity(signature,current) or current.st_size < signature[2]:
        raise UsageIndexTimeout('Usage history replaced or truncated; retry')
    if file_signature(path,current) == signature[:5]: return
    if current.st_size <= signature[2] or len(signature)!=6:
        raise UsageIndexTimeout('Usage history rewritten; retry')
    key = signature
    progress = _VERIFY_PROGRESS.get(key)
    version=file_signature(path,current)
    offset, digest = (progress[0],progress[1].copy()) if progress and progress[2]==version else (0,hashlib.sha256())
    with path.open('rb') as stream:
        if not same_identity(signature,os.fstat(stream.fileno())):
            raise UsageIndexTimeout('Usage history replaced; retry')
        stream.seek(signature[2]-1)
        if signature[2] and stream.read(1)!=b'\n':
            raise UsageIndexTimeout('Usage history partial row changed; retry')
        stream.seek(offset)
        while offset<signature[2]:
            if time.monotonic()>=deadline:
                if len(_VERIFY_PROGRESS)>=32:_VERIFY_PROGRESS.clear()
                _VERIFY_PROGRESS[key]=(offset,digest.copy(),version)
                raise UsageIndexTimeout('Usage prefix verification timed out; retry to continue')
            chunk=stream.read(min(8*1024*1024,signature[2]-offset))
            if not chunk:raise UsageIndexTimeout('Usage history truncated; retry')
            digest.update(chunk);offset+=len(chunk)
        after_fd=os.fstat(stream.fileno())
    after=path.stat()
    if not same_identity(signature,after_fd) or not same_identity(signature,after) or min(after.st_size,after_fd.st_size)<signature[2] or digest.hexdigest()!=signature[5]:
        _VERIFY_PROGRESS.pop(key,None)
        raise UsageIndexTimeout('Usage history prefix changed; retry')
    _VERIFY_PROGRESS.pop(key,None)
    _DEFERRED_TAIL.add(str(path))


def pending_accounting_capture(path, current):
    """A partial event cache retains its original proven range across appends."""
    cached=_FILE_PROJECTS.get(str(path))
    if cached and cached[0][:5]==current[:5]:return None
    if not cached:
        destination=cache_path(path)
        try:
            if destination is None or destination.is_symlink():return None
            stat=destination.stat()
            if stat.st_size>262144 or stat.st_uid!=os.getuid() or stat.st_mode&0o077:return None
            signature=tuple(json.loads(destination.read_text())['signature'])
            if len(signature)!=6 or signature[0]!=str(path) or not all(type(v) is int for v in signature[1:5]) or not isinstance(signature[5],str) or not re.fullmatch('[0-9a-f]{64}',signature[5]):return None
            disk=load_progress(path,signature)
            if not disk or not disk[3]:return None
            cached=(signature,disk[1],disk[2])
        except (OSError,ValueError,TypeError,KeyError):return None
    if len(cached[0])!=6:return None
    entry=load_events(path,cached[0])
    return cached if entry and not entry['complete'] else None


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
            signature = file_signature(path,stat)
            if project:
                account_pending=pending_accounting_capture(path,signature)
                if account_pending:
                    try:validate_prefix(path,account_pending[0],deadline)
                    except UsageIndexTimeout:
                        if account_pending[0] not in _VERIFY_PROGRESS:
                            _FILE_PROJECTS.pop(str(path),None)
                            destination=event_cache_path(path)
                            if destination is not None:
                                try:destination.unlink(missing_ok=True)
                                except OSError:pass
                        raise
                    _FILE_PROJECTS[str(path)]=account_pending
                    indexed.append((path,account_pending[0],account_pending[1],account_pending[2]))
                    continue
                pending=_PENDING_INDEX.get(str(path))
                if pending:
                    try:validate_prefix(path,pending[0],deadline)
                    except UsageIndexTimeout:
                        if pending[0] not in _VERIFY_PROGRESS:_PENDING_INDEX.pop(str(path),None)
                        raise
                    _FILE_PROJECTS[str(path)]=pending
                    _PENDING_INDEX.pop(str(path),None)
                    save_progress(path,pending[0],pending[0][2],pending[1],pending[2],complete=True)
                    indexed.append((path,pending[0],pending[1],pending[2]))
                    continue
                cached = _FILE_PROJECTS.get(str(path))
                disk = load_progress(path, signature) if cached is None or cached[0][:5] != signature[:5] else None
                if disk and disk[3]:
                    cached = (disk[4], disk[1], disk[2])
                    _FILE_PROJECTS[str(path)] = cached
                if cached is None or cached[0][:5] != signature[:5]:
                    progress = _FILE_PROGRESS.get(str(path))
                    if progress and progress[0] == signature:
                        offset, projects, threads = progress[1], set(progress[2]), set(progress[3]) if progress[3] is not None else None
                    elif disk:
                        offset, projects, threads = disk[:3]
                    else:
                        offset, projects, threads = 0, set(), {path.stem}
                    with path.open('rb') as raw:
                        stream=PrefixStream(raw,signature,deadline)
                        stream.seek(offset)
                        if disk and not disk[3] and len(disk[4])==6 and stream.hash.hexdigest()!=disk[4][5]:
                            raise UsageIndexTimeout('Usage checkpoint prefix changed; retry')
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
                            digest=stream.checkpoint_digest(offset)
                            save_progress(path, signature[:5]+(digest,) if digest else signature[:5], offset, projects, threads)
                            raise
                        signature = signature[:5]+(stream.complete_digest(),)
                    cached = (signature, projects or {'unknown'}, threads)
                    if len(_PENDING_INDEX)>=32:_PENDING_INDEX.clear()
                    _PENDING_INDEX[str(path)]=cached
                    try:validate_prefix(path,signature,deadline)
                    except UsageIndexTimeout:
                        if signature not in _VERIFY_PROGRESS:_PENDING_INDEX.pop(str(path),None)
                        raise
                    _PENDING_INDEX.pop(str(path),None)
                    if len(_FILE_PROJECTS) >= 4096:
                        _FILE_PROJECTS.clear()
                    _FILE_PROJECTS[str(path)] = cached
                    _FILE_PROGRESS.pop(str(path), None)
                    save_progress(path, signature, stat.st_size, cached[1], cached[2], complete=True)
                indexed.append((path, cached[0], cached[1], cached[2]))
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

EVENT_CACHE_BYTES = 32 * 1024 * 1024
EVENT_CACHE_COUNT = 100000


def normalized_events(obj):
    """Whitelist accounting inputs only; deltas and attribution stay in scan()."""
    if not isinstance(obj, dict): return []
    kind, payload = obj.get('type'), obj.get('payload', {})
    stamp = scalar(obj, 'timestamp')
    events = []
    if kind in ('session_meta', 'turn_context') and isinstance(payload, dict):
        allowed = META + ('id', 'parent_thread_id', 'agent_nickname', 'agent_role', 'turn_id')
        meta = {k:v for k,v in payload.items() if k in allowed and isinstance(v,(str,int))}
        source = payload.get('source')
        if isinstance(source,str): meta['source'] = 'root'
        elif isinstance(source,dict) and 'subagent' in source: meta['source'] = {'subagent':True}
        events.append({'type':kind,'timestamp':stamp,'payload':meta})
    if kind == 'event_msg' and isinstance(payload,dict) and payload.get('type') in ('task_complete','turn_aborted'):
        terminal = {'type':payload['type']}
        if isinstance(payload.get('turn_id'),str): terminal['turn_id'] = payload['turn_id']
        events.append({'type':'event_msg','timestamp':stamp,'payload':terminal})
    record = token_record(obj)
    if record:
        usage = {k:v for k,v in record['usage'].items() if k in COUNTERS and type(v) is int and v >= 0}
        if usage:
            if record['semantics'] == 'cumulative':
                events.append({'type':'event_msg','timestamp':record['timestamp'],'payload':{'type':'token_count','info':{'total_token_usage':usage}}})
            else:
                event = {k:v for k,v in record.items() if k in META and isinstance(v,(str,int))}
                event.update(type='token_usage_record',timestamp=record['timestamp'],usage=usage,request_id=record['event_id'],turn_id=record['turn_id'])
                events.append(event)
    return events


def event_cache_path(path):
    metadata = cache_path(path)
    if metadata is None: return None
    directory = metadata.parent / 'events'
    if directory.is_symlink(): return None
    try:
        directory.mkdir(mode=0o700,exist_ok=True)
        if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077: return None
    except OSError: return None
    return directory / metadata.name


def load_events(path, signature):
    destination = event_cache_path(path)
    try:
        if destination is None or destination.is_symlink() or not destination.is_file(): return None
        stat = destination.stat()
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_size > EVENT_CACHE_BYTES: return None
        entry = json.loads(destination.read_text())
        if set(entry) != {'schema','signature','offset','complete','events'} or entry['schema'] != 1 or entry['signature'][:5] != list(signature[:5]): return None
        if len(signature)==6 and entry['signature']!=list(signature):return None
        if type(entry['complete']) is not bool or type(entry['offset']) is not int or not 0 <= entry['offset'] <= signature[2]: return None
        if entry['complete'] and entry['offset'] != signature[2]: return None
        events = entry['events']
        if not isinstance(events,list) or len(events) > EVENT_CACHE_COUNT: return None
        for event in events:
            if event != {'type':'_usage_malformed'} and normalized_events(event) != [event]: return None
        if entry['offset'] and not entry['complete']:
            with path.open('rb') as stream:
                stream.seek(entry['offset']-1)
                if stream.read(1) != b'\n': return None
        return entry
    except (OSError,ValueError,TypeError,KeyError,AttributeError): return None


def save_events(path, signature, offset, events, complete=False):
    destination = event_cache_path(path)
    if destination is None or len(events) > EVENT_CACHE_COUNT: return
    temporary = None
    try:
        raw = json.dumps({'schema':1,'signature':list(signature),'offset':offset,'complete':complete,'events':events},separators=(',',':'))
        size = len(raw.encode())
        if size > EVENT_CACHE_BYTES or destination.is_symlink(): return
        siblings = [(p,p.stat().st_size,p.stat().st_mtime_ns) for p in destination.parent.glob('*.json') if p != destination]
        total, count = sum(item[1] for item in siblings), len(siblings)
        for old, old_size, _ in sorted(siblings,key=lambda item:item[2]):
            if count < 512 and total + size <= 128*1024*1024: break
            old.unlink();total -= old_size;count -= 1
        fd,temporary = tempfile.mkstemp(prefix='.events-',dir=destination.parent)
        with os.fdopen(fd,'w') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,destination);temporary = None
    except OSError: pass
    finally:
        if temporary:
            try: os.unlink(temporary)
            except OSError: pass


def accounting_objects(path, signature, stream, deadline):
    """Replay normalized events, then safely continue unfinished extraction."""
    raw=stream
    if signature is not None:stream=PrefixStream(raw,signature,deadline)
    cached = load_events(path,signature) if signature is not None else None
    events = cached['events'] if cached else []
    for event in events:
        if time.monotonic() >= deadline:
            raise UsageIndexTimeout('Usage event collection timed out; retry to continue observed counters')
        yield event
    if cached and cached['complete']:
        validate_prefix(path,signature,deadline)
        return
    offset = cached['offset'] if cached else 0
    stream.seek(offset)
    cacheable = signature is not None
    trailing = []
    while True:
        if time.monotonic() >= deadline:
            if cacheable: save_events(path,signature,offset,events)
            raise UsageIndexTimeout('Usage event collection timed out; retry to continue observed counters')
        line = stream.readline()
        if not line: break
        next_offset = stream.tell()
        complete_line = line.endswith(b'\n')
        extracted = []
        if not body_only(line):
            try: extracted = normalized_events(json.loads(line))
            except (json.JSONDecodeError,UnicodeError): extracted = [{'type':'_usage_malformed'}]
        if complete_line:
            offset = next_offset
            if cacheable:
                events.extend(extracted)
                if len(events) > EVENT_CACHE_COUNT:
                    cacheable = False;events = []
        for event in extracted: yield event
        if not complete_line:
            # EOF without a newline is valid only in a complete, unchanged file.
            trailing = extracted
    if signature is not None:
        digest=stream.complete_digest()
        if len(signature)==6 and digest!=signature[5]:
            raise UsageIndexTimeout('Usage history prefix changed during event collection; retry')
        signature=signature[:5]+(digest,)
        validate_prefix(path,signature,deadline)
    if cacheable:
        events.extend(trailing)
        save_events(path,signature,signature[2],events,complete=True)


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
    _DEFERRED_TAIL.clear()
    project = canonical_project(project)
    deadline = time.monotonic() + SCAN_SECONDS
    selected = project_files(root, project, deadline)
    cache_key = (str(root.resolve()), date_from, date_to, project, thread, tuple(signature for _, signature in selected))
    if project and _REPORT_CACHE is not None and _REPORT_CACHE[0] == cache_key:
        cached_report=copy.deepcopy(_REPORT_CACHE[1])
        cached_report['deferred_tail_files']=len(_DEFERRED_TAIL)
        if _DEFERRED_TAIL and 'verified captured prefix' not in cached_report['limitations']:
            cached_report['limitations'] += ' Growing files were read through a verified captured prefix; appended tail records are deferred until the next refresh.'
        return cached_report
    groups = defaultdict(lambda: defaultdict(int))
    records, raw_records, seen, previous = [], [], set(), {}
    turns, agents = {}, {}
    files = malformed = duplicates = resets = unreadable = 0
    for path, signature in selected:
        if time.monotonic() >= deadline:
            raise UsageIndexTimeout('Usage collection timed out; retry to collect observed counters')
        files += 1
        metadata = {'thread_id': path.stem}
        try:
            lines = path.open('rb')
        except OSError:
            unreadable += 1
            continue
        with lines:
            for obj in accounting_objects(path,signature,lines,deadline):
                if obj.get('type') == '_usage_malformed':
                    malformed += 1
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

    report['deferred_tail_files'] = len(_DEFERRED_TAIL)
    if _DEFERRED_TAIL:
        report['limitations'] += ' Growing files were read through a verified captured prefix; appended tail records are deferred until the next refresh.'
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
