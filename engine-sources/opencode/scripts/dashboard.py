#!/usr/bin/env python3
"""Open the distribution's local setup and usage dashboard for a chosen folder."""
from __future__ import annotations
import argparse, runpy, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target',type=Path)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args(argv)
    target=args.target.expanduser()
    if not target.is_dir(): parser.error('Choose an existing local project folder.')
    target=target.resolve()
    sys.path.insert(0,str(ROOT/'.opencode/tools'))
    sys.argv=[str(ROOT/'.opencode/tools/console.py'),'configure','--root',str(target),'--port',str(args.port),'--distribution-install',*(['--no-browser'] if args.no_browser else [])]
    runpy.run_path(str(ROOT/'.opencode/tools/console.py'),run_name='__main__')

if __name__=='__main__': main()
