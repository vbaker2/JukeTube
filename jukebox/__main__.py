"""Command-line entry point: `python -m jukebox`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .config import cache_dir, load_config


def list_inputs() -> int:
    """Print joystick/keyboard events so cabinet owners can map buttons."""
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 200))
    pygame.display.set_caption("Jukebox input test - press buttons, ESC twice to quit")
    print("Press keys / move sticks / press buttons. ESC twice to quit.")
    last_esc = 0.0
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return 0
            if e.type == pygame.KEYDOWN:
                print(f"key: {pygame.key.name(e.key)!r}")
                if e.key == pygame.K_ESCAPE:
                    if time.monotonic() - last_esc < 1:
                        return 0
                    last_esc = time.monotonic()
            elif e.type == pygame.JOYDEVICEADDED:
                js = pygame.joystick.Joystick(e.device_index)
                print(f"joystick connected: {js.get_name()} ({js.get_numbuttons()} buttons)")
            elif e.type == pygame.JOYBUTTONDOWN:
                print(f"joystick {e.instance_id} button: {e.button}")
            elif e.type == pygame.JOYHATMOTION:
                print(f"joystick {e.instance_id} hat {e.hat}: {e.value}")
            elif e.type == pygame.JOYAXISMOTION and abs(e.value) > 0.5:
                print(f"joystick {e.instance_id} axis {e.axis}: {e.value:+.2f}")
        time.sleep(0.01)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jukebox", description="Arcade-cabinet music jukebox")
    ap.add_argument("-c", "--config", help="path to config.toml")
    ap.add_argument("-w", "--windowed", action="store_true", help="run in a window instead of fullscreen")
    ap.add_argument("--demo", action="store_true", help="offline demo: fake catalogue, no audio")
    ap.add_argument("--silent", action="store_true", help="don't play audio (simulated player)")
    ap.add_argument("--free-play", action="store_true", help="force free play on")
    ap.add_argument("--list-inputs", action="store_true", help="show button/key codes for mapping")
    ap.add_argument("--screenshot", help=argparse.SUPPRESS)
    ap.add_argument("--frames", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    if args.list_inputs:
        return list_inputs()

    cfg = load_config(args.config)
    if args.windowed:
        cfg["display"]["fullscreen"] = False
    if args.free_play:
        cfg["jukebox"]["free_play"] = True

    import pygame

    from .player import create_player
    from .sources import create_source
    from .ui.app import App

    if args.demo:
        from .sources.demo import DemoSource

        source = DemoSource()
    else:
        src_cfg = cfg["source"]
        source_cfg = src_cfg.get(src_cfg["name"]) or {}
        opts = {k: v for k, v in source_cfg.items()
                if k != "profiles" and v != ""}
        profiles = source_cfg.get("profiles") or []
        if profiles:
            profile_file = Path(cfg["_path"]).with_name("profile.json")
            try:
                profile_id = json.loads(profile_file.read_text()).get("id")
            except (OSError, ValueError, AttributeError):
                profile_id = None
            profile = next((p for p in profiles if p.get("id") == profile_id), profiles[0])
            profile_auth = profile.get("auth_file", opts.get("auth_file", ""))
            opts["auth_file"] = os.path.expanduser(profile_auth) if profile_auth else ""
            if profile.get("user"):
                opts["user"] = str(profile["user"])
            cfg["_profile_id"] = profile.get("id", "")
        source = create_source(src_cfg["name"], opts)
    player, warning = create_player(cfg["audio"], simulate=args.demo or args.silent)

    pygame.init()
    app = App(cfg, source, player, cache_dir(), player_warning=warning,
              screenshot=args.screenshot, max_frames=args.frames)
    app.run()
    pygame.quit()
    if os.environ.pop("JUKEBOX_UPDATE_RESTART", None) == "1":
        os.execv(sys.executable, [sys.executable, "-m", "jukebox", *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
