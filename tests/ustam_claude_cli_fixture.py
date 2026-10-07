"""Disposable POSIX Claude metadata stub: cannot execute prompts or sessions."""
from pathlib import Path
from ustam.runtime import RuntimeAttention

def create_version_only_cli(directory):
    executable=Path(directory)/'metadata-bin'/'claude'
    executable.parent.mkdir(parents=True,exist_ok=True)
    executable.write_text('#!/bin/sh\nif [ "$#" -ne 1 ]; then exit 64; fi\ncase "$1" in\n  --version) printf "%s\\n" "2.1.287 (Claude Code fixture)" ;;\n  --help) printf "%s\\n" "Metadata-only fixture. Prompts and sessions are unavailable." ;;\n  *) exit 64 ;;\nesac\n')
    executable.chmod(0o700)
    return executable

def resolver(executable):
    def resolve(provider,**kwargs):
        if provider=='claude':return str(executable)
        raise RuntimeAttention('offline fixture')
    return resolve
