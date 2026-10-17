"""Configuration: built-in defaults deep-merged with an optional TOML file."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

# Keyboard defaults follow MAME's player-1 layout, which is what most arcade
# keyboard encoders (I-PAC, Xin-Mo in keyboard mode, etc.) emit.
DEFAULTS: dict = {
    "display": {
        "fullscreen": True,
        "width": 1280,  # logical resolution; scaled to the real screen
        "height": 720,
        "fps": 60,
        "hide_cursor": True,
        "title": "JukeTube",
        "attract_after": 45,  # seconds idle before the big now-playing screen
        "theme": "neon",
    },
    "audio": {
        "volume": 80,
        "volume_step": 5,
        "mpv_path": "",
        "ytdlp_path": "",
        "audio_device": "",
        # Stream as your YouTube account (Premium audio quality): name a browser
        # you're logged into YouTube Music with, or point at a cookies.txt.
        "cookies_from_browser": "",
        "cookies_file": "",
        "mpv_args": [],
    },
    "jukebox": {
        "free_play": True,
        "songs_per_credit": 1,
        "max_credits": 99,
        "max_queue": 50,
        "max_per_track_in_queue": 1,
        "allow_skip": True,
        "autoplay": False,
        "autoplay_delay": 3,
        "autoplay_seed": "",
        "autoplay_radio": False,
        "interrupt_autoplay": True,
    },
    "source": {
        "name": "ytmusic",
        "ytmusic": {
            "auth_file": "",
            "profiles": [],
            "language": "en",
            "location": "GB",  # region for the home feed and search results
            "charts": [
                {"name": "UK", "country": "GB"},
                {"name": "Russian", "country": "RU"},
                {"name": "Estonian", "country": "EE"},
            ],
            "allow_explicit": True,
        },
    },
    "controls": {
        "exit_hold_seconds": 1.0,  # hold Coin + Start on either panel to quit
        "keyboard": {
            "up": ["up"],
            "down": ["down"],
            "left": ["left"],
            "right": ["right"],
            "select": ["left ctrl", "return"],
            "back": ["left alt", "backspace"],
            "alt": ["space", "tab"],
            "skip": ["left shift"],
            "pause": ["z", "p"],
            "start": ["1"],
            "coin": ["5"],
            "volume_up": ["x", "=", "+", "page up"],
            "volume_down": ["c", "-", "page down"],
            "exit": ["escape"],
        },
        "joystick": {
            # Button numbers vary by encoder; run with --list-inputs to find yours.
            "select": [0],
            "back": [1],
            "alt": [2],
            "skip": [3],
            "pause": [4],
            "volume_down": [5],
            "volume_up": [6],
            "coin": [8],
            "start": [9],
            "deadzone": 0.5,
        },
        "repeat_delay": 0.35,
        "repeat_rate": 0.07,
    },
}


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (
        os.environ.get("APPDATA") if os.name == "nt" else None
    ) or os.path.join(Path.home(), ".config")
    return Path(base) / "jukebox" / "config.toml"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (
        os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    ) or os.path.join(Path.home(), ".cache")
    path = Path(base) / "jukebox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    candidate = Path(path) if path else default_config_path()
    if candidate.exists():
        with open(candidate, "rb") as f:
            _merge(cfg, tomllib.load(f))
    elif path:
        raise FileNotFoundError(f"Config file not found: {candidate}")
    cfg["_path"] = str(candidate)
    return cfg
