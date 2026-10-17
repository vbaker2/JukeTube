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


def cabinet_button(button, down=True, device=0):
    return pygame.event.Event(pygame.JOYBUTTONDOWN if down else pygame.JOYBUTTONUP,
                              instance_id=device, button=button)


def test_cabinet_exit_hold_and_single_taps():
    m = InputMapper(DEFAULTS["controls"])
    assert m.handle(cabinet_button(8), 0) == []
    assert m.handle(cabinet_button(8, False), 0.1) == [Action.COIN]
    assert m.handle(cabinet_button(9), 1) == []
    assert m.handle(cabinet_button(9, False), 1.1) == [Action.START]
    m.handle(cabinet_button(8), 2)
    m.handle(cabinet_button(9), 2.2)
    assert m.repeats(3.1) == []
    assert m.repeats(3.21) == [Action.QUIT]
    assert m.repeats(5) == []
    assert m.handle(cabinet_button(8, False), 5) == []
    assert m.handle(cabinet_button(9, False), 5) == []
    m.handle(cabinet_button(9), 6)
    assert m.handle(cabinet_button(9, False), 6.1) == [Action.START]


def test_cabinet_exit_requires_one_panel_and_cancels_on_release_or_disconnect():
    m = InputMapper(DEFAULTS["controls"])
    m.handle(cabinet_button(8, device=0), 0)
    m.handle(cabinet_button(9, device=1), 0)
    assert m.repeats(2) == []
    m.handle(cabinet_button(9, device=0), 2)
    assert m.handle(cabinet_button(8, False, device=0), 2.1) == []
    assert m.repeats(4) == []
    assert m.handle(cabinet_button(9, False, device=0), 4) == []
    m.handle(cabinet_button(8, device=1), 5)
    m.handle(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=1), 5.1)
    assert m.repeats(7) == []


def test_right_coin_can_switch_profiles_and_still_quit_with_start():
    m = InputMapper(DEFAULTS["controls"])
    m.set_device_button_action(1, 8, Action.PROFILE_NEXT)
    assert m.handle(cabinet_button(8, device=1), 0) == []
    assert m.handle(cabinet_button(8, False, device=1), 0.1) == [Action.PROFILE_NEXT]
    # The override is device-specific; left coin still adds credit.
    assert m.handle(cabinet_button(8), 1) == []
    assert m.handle(cabinet_button(8, False), 1.1) == [Action.COIN]
    # A Coin + Start chord on the right panel remains the keyboard-free quit.
    m.handle(cabinet_button(8, device=1), 2)
    m.handle(cabinet_button(9, device=1), 2.1)
    assert m.repeats(3.01) == []
    assert m.repeats(3.11) == [Action.QUIT]
    assert m.handle(cabinet_button(8, False, device=1), 3.2) == []
    assert m.handle(cabinet_button(9, False, device=1), 3.2) == []


def test_calibrated_right_button_chords_change_volume_and_singles_still_work():
    m = InputMapper(DEFAULTS["controls"])
    m.set_device_chord(0, (10, 11), Action.VOLUME_UP)
    m.set_device_chord(0, (11, 12), Action.VOLUME_DOWN)
    assert m.handle(cabinet_button(10), 0) == []
    assert m.handle(cabinet_button(11), 0.1) == [Action.VOLUME_UP]
    assert m.handle(cabinet_button(10, False), 0.2) == []
    assert m.handle(cabinet_button(11, False), 0.3) == []

    assert m.handle(cabinet_button(11), 1) == []
    assert m.handle(cabinet_button(11, False), 1.1) == []  # unmapped standalone

    m.set_device_button_action(0, 10, Action.SKIP)
    assert m.handle(cabinet_button(10), 2) == []
    assert m.handle(cabinet_button(10, False), 2.1) == [Action.SKIP]

    assert m.handle(cabinet_button(11), 3) == []
    assert m.handle(cabinet_button(12), 3.1) == [Action.VOLUME_DOWN]
    assert m.handle(cabinet_button(11, False), 3.2) == []
    assert m.handle(cabinet_button(12, False), 3.3) == []


def test_chord_shortcut_only_fires_once_and_discards_both_single_actions():
    m = InputMapper(DEFAULTS["controls"])
    m.set_device_button_action(0, 10, Action.SKIP)
    m.set_device_button_action(0, 11, Action.PAUSE)
    m.set_device_chord(0, (10, 11), Action.VOLUME_UP)
    assert m.handle(cabinet_button(10), 0) == []
    assert m.handle(cabinet_button(11), 0.1) == [Action.VOLUME_UP]
    assert m.handle(cabinet_button(10, False), 0.2) == []
    assert m.handle(cabinet_button(11, False), 0.3) == []


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


def test_account_cookie_args():
    from jukebox.player import ytdl_account_args

    assert ytdl_account_args({}) == []
    assert ytdl_account_args({"cookies_from_browser": "chrome"}) == [
        "--ytdl-raw-options-append=cookies-from-browser=chrome"]
    args = ytdl_account_args({"cookies_file": "~/c.txt", "cookies_from_browser": "chrome"})
    assert args == [f"--ytdl-raw-options-append=cookies={os.path.expanduser('~/c.txt')}"]


def test_bundled_fonts_render_cyrillic_and_estonian():
    from jukebox.ui.theme import FONTS

    # A font without a glyph draws the same "missing" box for every such
    # letter, so distinct letters rendering identically means they're missing.
    for path in FONTS.values():
        f = pygame.font.Font(path, 32)
        shapes = {pygame.image.tobytes(f.render(ch, True, (255, 255, 255)), "RGBA")
                  for ch in "ЖЯЮЩõšž"}
        assert len(shapes) == 7, path
