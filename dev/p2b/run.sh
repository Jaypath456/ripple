#!/bin/sh
# Launch the isolated pilot with the repository's existing virtual environment.
set -eu
P2B_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd -P)
P2B_GIT_DIR=$(git -C "$P2B_ROOT" rev-parse --path-format=absolute --git-common-dir)
P2B_PYTHON=${P2B_PYTHON:-${P2B_GIT_DIR%/.git}/.venv/bin/python}
if [ ! -x "$P2B_PYTHON" ]; then
    printf '%s\n' 'Set P2B_PYTHON to the existing Python environment.' >&2
    exit 2
fi
cd "$P2B_ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$P2B_ROOT/src${P2B_SDK_PATH:+:$P2B_SDK_PATH}"
exec "$P2B_PYTHON" -m ripple.pilot "$@"
