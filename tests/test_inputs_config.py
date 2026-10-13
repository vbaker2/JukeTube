import os

import pygame
import pytest

from jukebox.config import DEFAULTS, load_config
from jukebox.inputs import Action, InputMapper
from jukebox.player import SimulatedPlayer

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


@pytest.fixture(scope="module", autouse=True)
def _pygame():
    pygame.init()
    yield
    pygame.quit()


def key(k, down=True, unicode=""):
    return pygame.event.Event(pygame.KEYDOWN if down else pygame.KEYUP, key=k, unicode=unicode, mod=0)


def test_keyboard_mapping_and_repeat():
    m = InputMapper(DEFAULTS["controls"])
    assert m.handle(key(pygame.K_DOWN), 0.0) == [Action.DOWN]
    assert m.repeats(0.1) == []
    assert m.repeats(0.36) == [Action.DOWN]
    assert m.repeats(0.37) == []
    assert m.repeats(0.44) == [Action.DOWN]
    m.handle(key(pygame.K_DOWN, down=False), 0.5)
    assert m.repeats(1.0) == []


def test_buttons_do_not_repeat():
    m = InputMapper(DEFAULTS["controls"])
    assert m.handle(key(pygame.K_LCTRL), 0.0) == [Action.SELECT]
    assert m.repeats(5.0) == []


def test_typing_mode_swallows_printable_keys():
    m = InputMapper(DEFAULTS["controls"])
    assert m.handle(key(pygame.K_5, unicode="5"), 0) == [Action.COIN]
    m.typing = True
    assert m.handle(key(pygame.K_5, unicode="5"), 0) == []
    m.handle(pygame.event.Event(pygame.TEXTINPUT, text="5"), 0)
    assert m.handle(key(pygame.K_BACKSPACE), 0) == []
    assert m.take_text() == "5\b"
    assert m.handle(key(pygame.K_DOWN), 0) == [Action.DOWN]


def test_joystick_axis_hat_and_buttons():
    m = InputMapper(DEFAULTS["controls"])
    ax = lambda v: pygame.event.Event(pygame.JOYAXISMOTION, instance_id=0, axis=1, value=v)  # noqa: E731
    assert m.handle(ax(0.9), 0) == [Action.DOWN]
    assert m.handle(ax(0.95), 0) == []  # still held, no new press
    assert m.repeats(0.4) == [Action.DOWN]
    assert m.handle(ax(0.0), 0.5) == []
    assert m.repeats(2) == []
    assert m.handle(ax(-1.0), 3) == [Action.UP]
    hat = pygame.event.Event(pygame.JOYHATMOTION, instance_id=0, hat=0, value=(1, 0))
    assert m.handle(hat, 0) == [Action.RIGHT]
    btn = pygame.event.Event(pygame.JOYBUTTONDOWN, instance_id=0, button=0)
    assert m.handle(btn, 0) == [Action.SELECT]


def test_config_merge(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[jukebox]\nfree_play = false\n[controls.keyboard]\ncoin = ["c"]\n')
    cfg = load_config(p)
    assert cfg["jukebox"]["free_play"] is False
    assert cfg["jukebox"]["max_queue"] == 50  # default kept
    assert cfg["controls"]["keyboard"]["coin"] == ["c"]
    assert cfg["controls"]["keyboard"]["up"] == ["up"]


def test_missing_explicit_config_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_example_config_is_valid():
    path = os.path.join(os.path.dirname(__file__), "..", "config.example.toml")
    cfg = load_config(path)
    InputMapper(cfg["controls"])
    assert set(cfg) >= set(DEFAULTS)


def test_simulated_player_lifecycle():
    t = [0.0]
    p = SimulatedPlayer(default_length=10, clock=lambda: t[0])
    p.play("x")
    t[0] = 4
    assert p.is_busy() and p.position == 4
    p.set_paused(True)
    t[0] = 8
    assert p.position == 4
    p.set_paused(False)
    t[0] = 15
    assert p.take_finished() == "eof"
    assert not p.is_busy()


def test_mpv_ignores_stale_events_from_previous_track():
    import threading

    from jukebox.player import MpvPlayer

    p = MpvPlayer.__new__(MpvPlayer)  # skip subprocess startup
    p._state_lock = threading.Lock()
    p._busy, p._finished, p._awaiting_start = True, None, True
    p.position, p.duration, p.paused, p.error, p._loading_since = 0.0, None, False, None, 1.0
    p._handle({"event": "property-change", "name": "time-pos", "data": 300.0})
    p._handle({"event": "end-file", "reason": "eof"})
    assert p.position == 0.0 and p._finished is None  # stale, ignored
    p._handle({"event": "start-file"})
    p._handle({"event": "property-change", "name": "time-pos", "data": 1.5})
    assert p.position == 1.5 and p._loading_since is None
    p._handle({"event": "end-file", "reason": "eof"})
    assert p._finished == "eof" and not p._busy
