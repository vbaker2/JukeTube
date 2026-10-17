# Claude handover notes

Read [AGENTS.md](AGENTS.md) before changing this project. It contains the
project overview, owner preferences, architecture, testing commands, and the
required cabinet deployment briefing at `../arcade-cabinet-notes.md`.

## Current state

- The app is JukeTube by Vince Baker, a fullscreen pygame YouTube Music jukebox
  for the Lenovo arcade cabinet.
- The home Browse menu includes Charts, Genres, Artists, and (when signed in)
  Your Library. The Artists menu currently starts with Back Street Boys, The
  Prodigy, Knife Party, and Swedish House Mafia.
- Cabinet auto-update is enabled. It fetches `master`, runs the complete test
  suite on the cabinet, and applies a passing build at a track boundary. Avoid
  manually restarting JukeTube to deploy; Attract-Mode should remain running.
- The Artists menu build was deployed from commit
  `b75fe6fa1d8765d77e31726daa9803c7c8a37b93` on 2026-10-17. Confirm the
  cabinet's `~/.local/state/jukebox/installed-commit` before assuming a newer
  commit has applied.

## Handover workflow

Run the tests from `AGENTS.md` before committing. For cabinet deployment, read
`../arcade-cabinet-notes.md`, use the Lenovo at `mame@arcade`, and preserve the
Attract-Mode process. Check the updater log at
`~/.local/state/jukebox/app.log` and installed commit marker to verify rollout.
