#!/usr/bin/env python3
"""Native manual bridge: records local work, never calls a model or grants approval."""
import argparse
import json
import os
from pathlib import Path
import sys
from .protocol import WorkProtocol, ProtocolError


def state_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Ustam'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Ustam'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'ustam'


def main(provider=None, argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('action', choices=('list', 'create', 'revise', 'question', 'test_pack', 'prepare', 'freeze', 'check', 'reported_check', 'integration_check', 'cancel', 'resume'))
    if provider is None:
        parser.add_argument('--state-dir', type=Path, default=state_directory())
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--operation-id')
    parser.add_argument('--json', dest='payload', default='-', help='JSON file or - for stdin; no task text stored by default')
    arguments = list(sys.argv[1:] if argv is None else argv)
    for option in ('--project', '--state-dir', '--operation-id', '--json'):
        if sum(argument == option or argument.startswith(option + '=') for argument in arguments) > 1:
            parser.error('Repeated control option refused: ' + option)
    args = parser.parse_args(arguments)
    if provider is not None:
        args.state_dir = state_directory()
        if provider not in ('codex', 'claude', 'opencode', 'antigravity'):
            parser.error('Unsupported provider')
    else:
        provider = Path(__file__).parent.parent.name.lstrip('.')
    try:
        hubpath = args.state_dir / 'hub.json'
        if hubpath.is_symlink() or hubpath.stat().st_size > 1024 * 1024:
            raise ProtocolError('Invalid registry')
        registry = json.loads(hubpath.read_text())
        project = args.project.resolve(strict=True)
        entry = next((p for p in registry.get('projects', []) if p.get('path') == str(project)), None)
        if not entry:
            raise ProtocolError('Project is not registered in Ustam; installation does not start a job')
        protocol = WorkProtocol(args.state_dir / 'works')
        if args.action == 'list':
            result = {'works': protocol.list([entry['id']]), 'schema_version': 1}
        else:
            if args.payload != '-' and Path(args.payload).stat().st_size > 1024 * 1024:
                raise ProtocolError('Request exceeds bounds')
            content = sys.stdin.read(1024 * 1024 + 1) if args.payload == '-' else Path(args.payload).read_text()
            if len(content.encode()) > 1024 * 1024:
                raise ProtocolError('Request exceeds bounds')
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise ProtocolError('JSON object required')
            if args.action == 'create':
                payload.update(project_id=entry['id'], path=str(project), provider=provider)
            else:
                work = next((w for w in protocol.list([entry['id']]) if w['id'] == payload.get('id')), None)
                if not work or work['path'] != str(project):
                    raise ProtocolError('Work does not belong to registered project')
                if work['provider'] != provider:
                    raise ProtocolError('Use the tool installed for this work provider')
            result = {'work': protocol.mutate(args.action, payload, args.operation_id)}
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
        return 0
    except (ProtocolError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'ok': False, 'error': {'code': 'work_protocol', 'message': str(exc)}}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
