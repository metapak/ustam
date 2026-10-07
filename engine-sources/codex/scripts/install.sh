
#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    printf '%s\n' 'Error: Python 3.11 or newer was not found on PATH.' >&2
    exit 2
fi

if [ "$#" -eq 0 ]; then
    printf '%s\n' 'Ustam guided setup'
    printf '%s\n' 'Usage: scripts/install.sh /path/to/project --interactive'
    printf '%s\n' 'Native roles use OpenAI GPT models; external proposal APIs are opt-in.'
    exit 2
fi

exec "$PYTHON" "$SCRIPT_DIR/install.py" "$@"
