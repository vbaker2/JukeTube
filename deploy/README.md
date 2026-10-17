# Cabinet deployment

Read `../../arcade-cabinet-notes.md` (outside this repository) before accessing
the cabinet. Ask before stopping anything. The live target as of 13 October
2026 is the Lenovo, `mame@arcade`, not the planned MacBook replacement.

Installed on the Lenovo:

- Project: `/home/mame/jukebox`, with a Linux `.venv` using uv-managed Python 3.12.
- Audio: Ubuntu mpv; virtualenv `yt-dlp[default]`; Deno in `~/.local/bin`.
- Launch: `~/jukebox/cabinet-launch.sh`, called by Attract-Mode.
- Menu: **Fruit Machine → JukeTube**. Change to another display and back
  if the new entry is not visible.
- Emulator definition: `~/.attract/emulators/Jukebox.cfg`.
- Log: `~/.local/state/jukebox/app.log`.
- Config and local favourites: `~/.config/jukebox/` (created as needed).

`install_attract.py` installs the launcher, marker file and menu entry, backing
up changed existing files. It does not edit `attract.cfg`, launch a GUI, or stop
Attract. Run it on the cabinet after copying the source and installing the venv.
The launcher adds the local Deno runtime to PATH.

## Automatic updates

The cabinet launcher enables the in-app updater. It checks `master` every five
minutes using the repository-scoped read-only key at `~/.ssh/juketube_deploy`,
clones the candidate to `~/.local/state/jukebox/staged-release`, and runs the
cabinet test suite before marking a release ready. A release is installed only
when the current song ends. Any songs still queued are saved and restored
after restart, so updates do not wait for a long playlist to finish. The
current process then re-execs in place, so Attract-Mode remains the parent. Failed
checks or tests leave the installed code alone and are recorded in
`~/.local/state/jukebox/app.log`.

The cabinet needs `git`, `pytest` in its JukeTube virtualenv, `espeak-ng` (or
`espeak`) and `mpv`. Speech announces “Updating software” before a release is
installed. Never put a GitHub personal token on the cabinet; the deploy key is
read-only and can be revoked from repository settings independently.

Copy only source, packaging files and tests; never copy the Mac virtualenv,
cookies or auth files. After updating dependencies, use:

```sh
cd ~/jukebox
~/.local/bin/uv pip install --python .venv/bin/python -e '.[dev]'
touch .venv/.installed
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m pytest -q
```

Current validation: unit tests pass on Linux, demo rendering and live YouTube
Music search work, and mpv has streamed and decoded audio with `--ao=null`.
Audible output, launch/return through Attract, and physical button calibration
are pending the owner's permission for a live session. Attract has not been
stopped. The panel drawing follows the owner's photo; action labels on physical
positions must wait for calibration. Coin + Start held for one second on the
same encoder is the intended keyboard-free exit gesture.
