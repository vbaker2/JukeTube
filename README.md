# JukeTube by Vince Baker

A fullscreen, joystick-driven jukebox for arcade cabinets. It streams from
**YouTube Music** today; providers are pluggable so others can be added later.

- **Chart tabs** for the UK, Russia and Estonia (configurable), each opening on
  that country's Top 100. You can also browse British and European **genres** (Britpop, Madchester,
  2 Tone, Northern Soul, rave, Eurodance, Italo disco, Русский рок, Eesti muusika…) and YouTube Music's home shelves (UK
  region by default), or **search** with an on-screen arcade keyboard (or a real keyboard).
- Pick songs into a request queue; queue a whole playlist/album with one button.
- **Favourites**: save songs locally and pick them again from the home menu.
- **Popular Dance**: start a shuffled mix of mainstream UK dance and club
  tracks, sourced from Kiss Dance, UK dance hits and UK club anthem searches.
- **Credits**: free play, or coin-op mode where the COIN button buys songs.
- **House music**: optional autoplay from the UK Top 100. It is off by default;
  a song starts only after someone selects one.
- **Idle screen**: after 45s without input it shows the now-playing screen, or
  fades between cover art from completed songs when nothing is playing. Plays
  from genre playlists build a persistent most-played-genre ranking. Any button
  wakes the menu.
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

For YouTube playback, install [Deno](https://deno.com/) and keep it on PATH.
The Python dependencies include yt-dlp's JavaScript challenge solver.

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

The joystick and button guide is visible beside browsing and playback by default,
including in attract mode. Press **Start** or open **Settings** from the home
screen for volume, profiles, the control guide and playback options. Choose
**Show / hide controls** there to hide the guide or bring it back. This choice
survives restarts in `controls-visible.json` beside your config file. The larger
**Controls** guide displays your configured button IDs and keyboard mappings.
The diagram follows
the cabinet photo: two outer 8-way sticks, a central analogue stick, two groups of
three diagonal buttons and four top buttons. Button wiring still needs live
calibration before actions can be labelled on the physical positions.

**Quit without a keyboard:** hold **Coin + Start together on the same panel for
one second**. This exits directly, including from the idle screen. Single Coin
and Start taps take effect when released. On the calibrated cabinet, the
right-hand COIN button cycles family profiles; the left-hand COIN button adds
credit. **Settings → Playback controls** also
provides skip, pause, volume and an exit confirmation using just the stick and
SELECT/BACK buttons.

Skip and volume work from every screen, including the full-screen music
visualiser. On the calibrated right-hand three-button group, hold the upper two
buttons together for volume up, or the lower two together for volume down. A
single button in either pair keeps its calibrated action.

| Action | Keyboard (MAME P1) | Joystick | What it does |
|---|---|---|---|
| Up/Down | ↑ ↓ | stick / d-pad | Move (hold to scroll) |
| Left/Right | ← → | stick / d-pad | Page up/down in lists; move on keyboard |
| SELECT | Left Ctrl, Enter | button 0 | Open / pick a song / type a key |
| BACK | Left Alt, Backspace | button 1 | Go back |
| ALT | Space, Tab | button 2 | Favourite/unfavourite a song; queue a whole playlist or album; type a space in search |
| SKIP | Left Shift | button 3 | Skip the current song (if `allow_skip`) |
| PAUSE | Z, P | button 4 | Pause/resume |
| VOL −/+ | C / X (or −/+) | buttons 5/6 | Volume |
| COIN | 5 | button 8 | Add credit (left cabinet control); right cabinet control cycles profiles |
| START | 1 | button 9 | Open Settings; in search, run it |
| EXIT | Esc | – | Exit (asks first) |

Every mapping can be changed in the config. Run `--list-inputs` to find your
button numbers or key names.

## Configure

### Favourites

Highlight a song in search results, a playlist or **Up Next**, then press
**Space/Tab** (joystick **button 2**) to save it. Press again to remove it.
Saved songs show **FAV**. Open **Favourites** on the home menu and press SELECT
to request a song using the normal queue and credit rules.

Favourites survive restarts in `favourites.json` beside your config file
(normally `~/.config/jukebox/favourites.json`). Demo songs are shown separately
from YouTube Music favourites. These are local favourites, not YouTube Music
account likes.

### Skins

The two built-in skins use the same layout and controls:

| Config value | Appearance |
|---|---|
| `neon` | Dark purple with hot pink and cyan highlights (default) |
| `classic` | Warm brown with amber and gold highlights, inspired by pub jukeboxes |

Set `theme = "neon"` or `theme = "classic"` under `[display]` in your config,
then restart the jukebox.

### Family YouTube Music profiles

For supervised children in a Google family group, each child has a separate
Google Account and personal YouTube Music library. Create one private browser
auth file while signed into each child's account, then add one profile entry
per account:

```toml
[[source.ytmusic.profiles]]
id = "parent"
name = "Parent"
auth_file = "~/jukebox-auth/parent.json"

[[source.ytmusic.profiles]]
id = "child-one"
name = "Child One"
auth_file = "~/jukebox-auth/child-one.json"
```

On the signed-in account, capture a successful `/browse` request's headers in
browser developer tools, then run `.venv/bin/ytmusicapi browser --file
~/jukebox-auth/child-one.json` with those headers on standard input (on macOS,
`pbpaste |` can supply them). Repeat for each account. Choose **Family profiles**
in Settings to change whose library and personalised feed you browse. Search,
charts and public playlists remain available without an auth file. This does
not change playback cookies.

On the cabinet, save the auth file(s) under `/home/mame/jukebox-auth/` and add
profile entries to `/home/mame/.config/jukebox/config.toml`. Create each auth
file on a signed-in browser with `.venv/bin/ytmusicapi browser`; copy only those
private files directly to the cabinet, never into the source repo.

Use **Settings → Volume − / Volume +** to adjust the level. The current volume
is also shown in the now-playing panel.

Use **Settings → Arrange home menu** or **Arrange genres** to change their
order with LEFT/RIGHT, choose SELECT to save, or BACK to cancel. The order is
saved locally in `ui-order.json` alongside the jukebox configuration.

### Settings

Copy `config.example.toml` to `~/.config/jukebox/config.toml`
(`%APPDATA%\jukebox\config.toml` on Windows) or pass `--config path`. Common
tweaks:

```toml
[jukebox]
free_play = false        # coin-op mode
songs_per_credit = 3     # 3 songs per coin
allow_skip = false       # no skipping paid songs

[source.ytmusic]
location = "US"          # region for home feed and search
allow_explicit = false

[[source.ytmusic.charts]] # chart tabs, in order; replaces the default UK/RU/EE set
name = "US"
country = "US"

[display]
theme = "classic"        # warm wood-and-chrome colours instead of neon
```

### Using your YouTube Music Premium account

Out of the box the jukebox uses **no account**: browsing and playback are
anonymous, at standard audio quality. There are two separate places an
account can plug in:

1. **Playback (Premium sound quality).** yt-dlp streams using your account's
   cookies. Set one of these under `[audio]`:
   ```toml
   cookies_from_browser = "chrome"   # a browser on this machine signed in to music.youtube.com
   # or
   cookies_file = "~/jukebox-cookies.txt"   # exported from a signed-in browser
   ```
   On a cabinet with no browser, sign in on another computer, export
   `cookies.txt` for youtube.com (e.g. with a "Get cookies.txt LOCALLY"
   extension), copy it over and use `cookies_file`. Cookies expire every few
   months; re-export if playback starts failing.
2. **Browsing (your library and personal home feed).** Run
   `.venv/bin/ytmusicapi browser`, follow its prompts (it asks you to paste
   request headers from a signed-in browser), and set `auth_file` under
   `[source.ytmusic]` to the file it writes. This adds "Your Library" and
   makes the home shelves follow your own listening.

Keep these files private: they give access to your Google account.

## Putting it on the cabinet

- **Front-ends** (LaunchBox/BigBox, AttractMode, RetroPie/EmulationStation): add
  `run.sh` (or `.venv\Scripts\jukebox.exe`) as a game/app entry. Exit returns to the front-end.
- **Dedicated jukebox mode on Linux**: start it at login, e.g. with an autostart
  `.desktop` entry that runs `/path/to/jukebox/run.sh`.
- Set `audio_device` if sound goes to the wrong output (`mpv --audio-device=help`
  lists them).

On the Lenovo cabinet, the Attract-Mode launcher checks the GitHub `master`
branch every five minutes, runs the test suite on a staged release, and applies
it after the current song and requested queue finish. It announces “Updating
software” first, then restarts JukeTube in place without stopping Attract-Mode.

## Keeping it working

YouTube changes things often, and the two unofficial libraries this relies on
get updated to keep up. If search or playback stops working, update them first:

```bash
.venv/bin/pip install -U 'yt-dlp[default]' ytmusicapi
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
- Fonts: Russo One, Fira Sans Condensed and Pacifico, all SIL Open Font
  License (see `jukebox/assets/fonts`). Russo One and Fira Sans cover Cyrillic
  and Estonian letters; Pacifico is used for the short Latin-script byline.
