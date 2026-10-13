# Arcade Jukebox

A fullscreen, joystick-driven jukebox for arcade cabinets. It streams from
**YouTube Music** today; providers are pluggable so others can be added later.

- Browse YouTube Music home shelves, global/country **charts**, **genres**, or
  **search** with an on-screen arcade keyboard (or a real keyboard).
- Pick songs into a request queue; queue a whole playlist/album with one button.
- **Credits**: free play, or coin-op mode where the COIN button buys songs.
- **House music**: when nobody's picked anything, it autoplays radio based on
  the last song. A paid request cuts in straight away.
- **Attract mode**: after 45s without input it shows a big now-playing screen
  with the album art, and the first button press wakes it.
- MAME-style default key mapping, so it works with I-PAC-style keyboard encoders out
  of the box; USB joystick encoders (sticks, hats, buttons) are supported too.
- Renders at 1280×720 and scales to whatever monitor the cabinet has.

## How it works

| Piece | Job |
|---|---|
| `pygame-ce` | Fullscreen UI and joystick/keyboard input |
| `ytmusicapi` | YouTube Music catalogue: search, charts, playlists, radio |
| `mpv` + `yt-dlp` | Audio playback; mpv runs headless and is driven over JSON IPC |

```
jukebox/
  __main__.py        CLI entry point (`python -m jukebox` / `jukebox`)
  config.py          defaults + TOML loading
  engine.py          queue, credits, autoplay (UI-agnostic, unit tested)
  player.py          MpvPlayer (real audio) / SimulatedPlayer (silent)
  inputs.py          keyboard + joystick -> actions, with auto-repeat
  sources/
    base.py          the Source interface new providers implement
    ytmusic.py       YouTube Music
    demo.py          offline fake catalogue for testing
  ui/                app shell, screens, theme, album-art cache
```

## Install

Requires Python 3.11+ and **mpv**.

```bash
# mpv
sudo apt install mpv            # Debian / Ubuntu / Raspberry Pi OS
brew install mpv                # macOS
scoop install mpv               # Windows (or download from mpv.io)

# the jukebox
cd jukebox
python3 -m venv .venv
.venv/bin/pip install -e .      # Windows: .venv\Scripts\pip install -e .
```

## Run

```bash
./run.sh                        # fullscreen (same as .venv/bin/jukebox)
./run.sh --windowed             # in a window, for testing at a desk
./run.sh --demo --windowed      # offline: fake songs, no audio, no network
./run.sh --list-inputs          # show what your cabinet's buttons send
```

If mpv isn't found, the jukebox still runs, but silently, and shows a warning on screen.

## Controls

| Action | Keyboard (MAME P1) | Joystick | What it does |
|---|---|---|---|
| Up/Down | ↑ ↓ | stick / d-pad | Move (hold to scroll) |
| Left/Right | ← → | stick / d-pad | Page up/down in lists; move on keyboard |
| SELECT | Left Ctrl, Enter | button 0 | Open / pick a song / type a key |
| BACK | Left Alt, Backspace | button 1 | Go back |
| ALT | Space, Tab | button 2 | Queue a whole playlist or album; type a space in search |
| SKIP | Left Shift | button 3 | Skip the current song (if `allow_skip`) |
| PAUSE | Z, P | button 4 | Pause/resume |
| VOL −/+ | C / X (or −/+) | buttons 5/6 | Volume |
| COIN | 5 | button 8 | Add credit (coin-op mode) |
| START | 1 | button 9 | Jump to search; in search, run it |
| EXIT | Esc | – | Exit (asks first) |

Every mapping can be changed in the config. Run `--list-inputs` to find your
button numbers or key names.

## Configure

Copy `config.example.toml` to `~/.config/jukebox/config.toml`
(`%APPDATA%\jukebox\config.toml` on Windows) or pass `--config path`. Common
tweaks:

```toml
[jukebox]
free_play = false        # coin-op mode
songs_per_credit = 3     # 3 songs per coin
allow_skip = false       # no skipping paid songs

[source.ytmusic]
chart_country = "GB"     # local charts instead of global
allow_explicit = false

[display]
theme = "classic"        # warm Wurlitzer colours instead of neon
```

**Optional YouTube Music sign-in.** Search, charts, genres and playback all
work without an account. To add a "Your Library" menu with your own
playlists, run `.venv/bin/ytmusicapi browser`, follow its prompts, and set
`auth_file` under `[source.ytmusic]` to the file it writes.

## Putting it on the cabinet

- **Front-ends** (LaunchBox/BigBox, AttractMode, RetroPie/EmulationStation): add
  `run.sh` (or `.venv\Scripts\jukebox.exe`) as a game/app entry. Exit returns to the front-end.
- **Dedicated jukebox mode on Linux**: start it at login, e.g. with an autostart
  `.desktop` entry that runs `/path/to/jukebox/run.sh`.
- Set `audio_device` if sound goes to the wrong output (`mpv --audio-device=help`
  lists them).

## Keeping it working

YouTube changes things often, and the two unofficial libraries this relies on
get updated to keep up. If search or playback stops working, update them first:

```bash
.venv/bin/pip install -U yt-dlp ytmusicapi
```

The source is written to degrade gracefully: if one YouTube Music endpoint
breaks, only that menu fails, with a message and a retry, rather than the whole
jukebox.

## Adding another music source

Implement `jukebox.sources.base.Source` (`home`, `search`, `expand`, `radio`,
`playable_url`) and register it in `jukebox/sources/__init__.py`. Anything mpv
can open (local files, HTTP streams, other yt-dlp sites) works as a
`playable_url`. See `sources/demo.py` for a minimal example.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Notes

- `ytmusicapi` and `yt-dlp` are unofficial clients. Use this with your own
  account/subscription, for personal use, in line with YouTube's terms.
- Tested on macOS with real playback. Linux should work the same way. On
  Windows, the mpv named-pipe IPC path is implemented but hasn't been tested yet.
- Fonts: Orbitron and Barlow Semi Condensed, both SIL Open Font License (see
  `jukebox/assets/fonts`).
