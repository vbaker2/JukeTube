#!/usr/bin/env sh
# Launch the jukebox from its virtualenv; handy as a front-end/autostart entry.
# On first run (or after dependencies change) it creates .venv and installs
# everything into it.
cd "$(dirname "$0")" || exit 1

VENV=.venv
PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$VENV/Scripts/python.exe"  # Git Bash on Windows

if [ ! -x "$PY" ] || [ pyproject.toml -nt "$VENV/.installed" ]; then
    BASE=""
    for c in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$c" >/dev/null 2>&1 &&
            "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            BASE="$c"
            break
        fi
    done
    if [ -z "$BASE" ]; then
        echo "jukebox: Python 3.11 or newer is required." >&2
        exit 1
    fi
    echo "jukebox: setting up $VENV (first run only)..." >&2
    [ -d "$VENV" ] || "$BASE" -m venv "$VENV" || exit 1
    PY="$VENV/bin/python"
    [ -x "$PY" ] || PY="$VENV/Scripts/python.exe"
    "$PY" -m pip install --quiet --upgrade pip &&
        "$PY" -m pip install --quiet -e . || exit 1
    touch "$VENV/.installed"
fi

if ! command -v mpv >/dev/null 2>&1; then
    echo "jukebox: mpv not found - music will be silent. Install it (e.g. 'brew install mpv' or 'sudo apt install mpv')." >&2
fi

exec "$PY" -m jukebox "$@"
