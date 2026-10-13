#!/usr/bin/env sh
# Launch the jukebox from its virtualenv; handy as a front-end/autostart entry.
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python -m jukebox "$@"
fi
exec python3 -m jukebox "$@"
