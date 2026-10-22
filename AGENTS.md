# AGENTS.md: Arcade Jukebox

Guidance for coding agents (Codex etc.) working on this repo. Read README.md
for the user-facing overview; this file covers what you need in order to change
the code safely.

For cabinet deployment, read `../arcade-cabinet-notes.md` first for SSH access, Attract-Mode integration and house rules.

## What this is

A fullscreen pygame jukebox for a home arcade cabinet, driven by a joystick
and buttons. Music comes from YouTube Music: `ytmusicapi` handles catalogue,
search and charts, and `mpv` with `yt-dlp` handles audio.

## Owner preferences (important)

- **British/European, not American.** The owner explicitly dislikes an
  "Americanised" jukebox. Genres are British/European (Britpop, Madchester,
  2 Tone, Northern Soul, rave, Eurodance, Italo disco, Русский рок, Eesti
  muusika…). Hip-hop, R&B, US country and similar styles are kept out via
  `DEFAULT_GENRE_EXCLUDE`. Use British English in UI text and docs.
- **Region is the UK by default** (`location = "GB"`). Chart tabs are UK,
  Russia and Estonia, in that order. House music (autoplay when the queue is
  empty) is the shuffled UK Top 100.
- **Cyrillic and Estonian text must render.** Russian titles are common.
  Bundled fonts must cover Cyrillic and Latin Extended (õ ä ö ü š ž);
  `tests/test_inputs_config.py::test_bundled_fonts_render_cyrillic_and_estonian`
  guards this. Never switch to a Latin-only font.
- The owner has a **YouTube Music Premium** account; see "Accounts" below.

## Commands

```bash
./run.sh --demo --windowed      # offline fake catalogue, no audio (creates .venv on first run)
./run.sh --windowed             # real YouTube Music + audio (needs mpv on PATH)
./run.sh --list-inputs          # print key/joystick codes for mapping cabinet buttons
.venv/bin/python -m pytest -q   # tests (set SDL_VIDEODRIVER=dummy when headless)
```

Headless screenshot, useful for checking UI changes without a display:

```bash
SDL_VIDEODRIVER=dummy .venv/bin/python -m jukebox --demo -w --frames 120 --screenshot out.png
```

(`--frames`/`--screenshot` are hidden flags: they render N frames, save the
last one and exit. To exercise navigation, drive `App.dispatch(Action.X)` from
a script and save `app.screen` with `pygame.image.save`.)

## Layout

```
jukebox/
  __main__.py       CLI; builds source + player + App
  config.py         DEFAULTS dict, deep-merged with ~/.config/jukebox/config.toml
  models.py         Track / Collection / Shelf (source-agnostic)
  engine.py         Jukebox: queue, credits, skip, autoplay. Pure logic, no pygame
  player.py         MpvPlayer (mpv JSON IPC), SimulatedPlayer, ytdl_account_args
  inputs.py         keyboard/joystick -> Action, auto-repeat, typing mode
  sources/base.py   Source ABC: home, search, expand, radio, playable_url, house_music
  sources/ytmusic.py  YouTube Music: genres, charts, parsing
  sources/demo.py   offline fake source
  ui/app.py         main loop, chrome (header, Up Next, now playing, toasts, attract, exit dialog)
  ui/screens.py     ListScreen, HomeScreen, QueueScreen, SearchScreen (on-screen keyboard)
  ui/theme.py       colours, fonts, glow helpers
  ui/art.py         album art: threaded fetch, disk cache, main-thread decode
config.example.toml documented config; keep it in sync with config.DEFAULTS
                    (a test loads it)
```

## How things work / gotchas

- **Threading:** network calls never run on the UI thread. Screens call
  `app.submit(fn)` (a ThreadPoolExecutor) and poll the Future in `update()`.
  pygame surfaces are only created and converted on the main thread.
- **ytmusicapi is fragile:** it scrapes YouTube and individual endpoints
  break (e.g. `get_mood_playlists` is currently broken). Every call goes
  through `YouTubeMusicSource._safe`, so one broken endpoint fails one menu,
  not the app. Keep that pattern.
- **Genres** are `{name, about, query, match}` dicts. Expansion searches
  featured *and* community playlists, keeps only titles containing a `match`
  keyword, and drops titles hitting `genre_exclude`. YouTube's playlist search
  pads niche queries with unrelated regional playlists (Punjabi, Bollywood,
  US country), so the keyword filter matters. Check genre changes against
  the live API (see below).
- **Charts:** `get_charts(country)` returns playlists; the "Top 100 Music
  Videos <country>" one is opened directly, with others after it.
- **mpv IPC:** two connections. One is owned by a reader thread for events;
  the other sends fire-and-forget commands (needed for Windows named pipes).
  After `loadfile`, events from the *previous* file are ignored until
  `start-file` arrives (`_awaiting_start`). Don't remove that, or skips
  will randomly end the new track.
- **Coin + Start quit must stay immediate.** Holding Coin + Start for 1s on one
  panel exits JukeTube at once so Attract-Mode's menu returns. Never put a
  confirmation dialog in front of it: the cabinet has no keyboard, and on
  2026-10-18 a "SELECT to confirm" dialog left the owner rebooting the cabinet.
  Plain buttons 8 + 9 always count for this, regardless of calibration.
- **Exiting must never block.** Start pygame with `init_pygame()` (display, font,
  joystick), never `pygame.init()`: its unused SDL mixer deadlocked in
  `pygame.quit()` on PulseAudio on 2026-10-22 and hung the cabinet. `finish()`
  in `__main__.py` ends the process (or re-execs for updates) with a
  3-second watchdog; keep every exit path going through it.
- **Never key cabinet calibration on SDL instance ids.** They depend on
  enumeration order: the UltraStik usually connects first and takes instance 0,
  pushing the DragonRise panels to 1 and 2. Calibration is resolved through
  slots (`guid`, ordinal among same-guid devices); see `calibration_slots`.
- **Input typing mode:** on the search screen, printable keys type rather
  than trigger their mapped action, and Backspace deletes. BACK always leaves
  search; the query persists in `app.last_query`.
- **Engine ordering:** paid requests go ahead of autoplay filler, and by
  default a request interrupts filler that's playing (`interrupt_autoplay`).

## Accounts

There is no account by default, so everything is anonymous. Two independent
hooks exist:
- Playback: `[audio] cookies_from_browser` or `cookies_file`. These are passed
  to yt-dlp via mpv `--ytdl-raw-options-append`, and are what give Premium
  audio quality.
- Browsing: `[source.ytmusic] auth_file` (from `ytmusicapi browser`). This adds
  "Your Library" and a personalised home feed.
Never commit cookie or auth files.

## Testing expectations

- Run `pytest` before committing; everything should pass. Add tests for
  engine/source logic using the fakes in `tests/` (no network in unit tests).
- For YouTube-facing changes, also check against the live API with a
  throwaway script (e.g. expand every genre and print the first titles).
  The API changes over time, so re-check rather than trusting old results.
- For UI changes, render a headless screenshot and look at it.

## Known gaps / ideas

- Windows: the mpv named-pipe path is implemented but untested.
- Not yet tested on the real cabinet hardware (button numbers, screen size).
- Chart "tabs" are rows in the home list. The owner said "tab", so a real
  left/right tab bar on the home screen may be wanted.
- Possible: house music drawn from the British/European genres instead of the
  UK chart (the UK chart includes American acts).
