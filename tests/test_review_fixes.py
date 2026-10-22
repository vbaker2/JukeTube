"""Regression tests for bugs found in the 2026-10-18 code review."""

import os
from collections import deque
from concurrent.futures import Future

import pygame
import pytest

from jukebox.config import DEFAULTS
from jukebox.engine import FETCH_INTERVAL, EngineConfig, Jukebox
from jukebox.inputs import Action, InputMapper
from jukebox.player import SimulatedPlayer
from jukebox.sources.demo import DemoSource, _track
from jukebox.updater import UpdateAgent

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
DR = "dragonrise-guid"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def sync_submit(fn, *args):
    f = Future()
    f.set_result(fn(*args))
    return f


def make(source=None, **cfg):
    clock = Clock()
    player = SimulatedPlayer(default_length=100, clock=clock)
    eng = Jukebox(source or DemoSource(), player, EngineConfig(**cfg), submit=sync_submit, clock=clock)
    return eng, player, clock


def test_queue_all_with_nothing_new_keeps_house_music_playing():
    eng, _, _ = make(autoplay=True, autoplay_delay=0)
    eng.tick()
    eng.tick()
    filler = eng.now_playing
    assert filler and not filler.requested
    added, _ = eng.request_many([])
    assert added == 0
    assert eng.now_playing is filler


def test_empty_autoplay_results_are_rate_limited():
    class Empty(DemoSource):
        calls = 0

        def house_music(self):
            Empty.calls += 1
            return []

    eng, _, clock = make(Empty(), autoplay=True, autoplay_delay=0)
    for _ in range(100):  # 100 frames
        eng.tick()
    assert Empty.calls == 1
    clock.t += FETCH_INTERVAL + 0.1
    eng.tick()
    assert Empty.calls == 2


def test_failed_play_backs_off_instead_of_draining_queue():
    eng, player, _ = make(autoplay=False)

    def broken(url):
        raise RuntimeError("Audio player unavailable")

    player.play = broken
    for i in range(10):
        eng.request(_track(i))
    for _ in range(50):
        eng.tick()
    assert len(eng.up_next) >= 4  # stops after ~5 failures, not all 10


@pytest.fixture(scope="module", autouse=True)
def _pygame():
    pygame.init()
    yield
    pygame.quit()


def btn(button, down=True, device=1):
    return pygame.event.Event(pygame.JOYBUTTONDOWN if down else pygame.JOYBUTTONUP,
                              instance_id=device, button=button)


def test_panel_replug_keeps_its_own_calibration():
    m = InputMapper(DEFAULTS["controls"])
    m.add_device(1, DR)  # left panel, ordinal 0
    m.add_device(2, DR)  # right panel, ordinal 1
    m.set_device_button_action((DR, 1), 8, Action.PROFILE_NEXT)
    m.remove_device(1)  # left panel drops off the hub
    assert m.button_action(2, 8) == Action.PROFILE_NEXT  # right keeps its mapping
    m.add_device(5, DR)  # left panel returns with a new instance id
    assert m.button_action(5, 8) == Action.COIN
    assert m.button_action(2, 8) == Action.PROFILE_NEXT


def test_duplicate_device_added_is_ignored():
    m = InputMapper(DEFAULTS["controls"])
    m.add_device(1, DR)
    m.add_device(1, DR)
    m.add_device(2, DR)
    assert m.instance_for((DR, 1)) == 2


def test_single_button_counting_as_coin_and_start_does_not_quit():
    m = InputMapper(DEFAULTS["controls"])
    m.add_device(1, DR)
    # Button 9 is Start by default; calibrated as the profile (coin) button.
    m.set_device_button_action((DR, 0), 9, Action.PROFILE_NEXT)
    m.handle(btn(9), 0)
    assert m.repeats(2) == []
    assert m.handle(btn(9, False), 2.1) == [Action.PROFILE_NEXT]


def test_failed_commit_is_not_retested(tmp_path, monkeypatch):
    agent = UpdateAgent(tmp_path, tmp_path / "state")
    agent._failed_commit = "abc123"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd[:2])

        class R:
            stdout = "abc123\trefs/heads/master\n"
        return R()

    monkeypatch.setattr("jukebox.updater.subprocess.run", fake_run)
    agent._stage_latest()
    assert calls == [["git", "ls-remote"]]  # no clone, no pytest


def test_announcement_failure_does_not_block(tmp_path, monkeypatch):
    agent = UpdateAgent(tmp_path, tmp_path / "state")
    monkeypatch.setattr("jukebox.updater.shutil.which", lambda name: None)
    agent._announce()  # must not raise


def test_credits_survive_update_restart(tmp_path):
    state = tmp_path / "state"
    agent = UpdateAgent(tmp_path, state)
    eng = type("E", (), {"queue": deque(), "credits": 0})()
    (state / "update-credits.json").write_text("3")
    agent.restore_queue(eng)
    assert eng.credits == 3
    assert not (state / "update-credits.json").exists()


def test_art_cache_is_bounded_and_retries_failures(tmp_path, monkeypatch):
    from jukebox.ui import art as art_mod

    pygame.display.set_mode((10, 10))
    cache = art_mod.ArtCache(tmp_path)
    surf = pygame.Surface((8, 8))
    png = tmp_path / "x.png"
    pygame.image.save(surf, str(png))
    data = png.read_bytes()

    def done(value):
        f = Future()
        f.set_result(value)
        return f

    for i in range(art_mod.MAX_RAW + 20):
        cache._pending[f"u{i}"] = done(data)
        assert cache.get(f"u{i}", 4) is not None
    assert len(cache._raw) == art_mod.MAX_RAW

    cache._pending["bad"] = done(None)
    assert cache.get("bad", 4) is None
    assert "bad" in cache._failed
    monkeypatch.setattr(art_mod.time, "monotonic", lambda: 1e12)
    cache.get("bad", 4)  # retry allowed now: a new fetch is started
    assert "bad" in cache._pending
    cache.shutdown()


def test_mpv_command_restarts_dead_player(monkeypatch):
    import threading

    from jukebox.player import MpvPlayer

    p = MpvPlayer.__new__(MpvPlayer)
    p._state_lock = threading.Lock()
    p._spawn_lock = threading.Lock()
    p.error = None
    sent = []

    class Dead:
        def send(self, cmd):
            raise BrokenPipeError

    class Alive:
        def send(self, cmd):
            sent.append(cmd)

    p._cmd = Dead()
    restarts = []
    monkeypatch.setattr(p, "_close_process", lambda: None, raising=False)

    def spawn():
        restarts.append(1)
        p._cmd = Alive()

    monkeypatch.setattr(p, "_spawn", spawn, raising=False)
    p.set_paused(True)  # must not raise
    assert restarts == [1]
    assert sent == [["set_property", "pause", True]]


def test_mpv_unrecoverable_does_not_crash_controls(monkeypatch):
    import threading

    from jukebox.player import MpvPlayer

    p = MpvPlayer.__new__(MpvPlayer)
    p._state_lock = threading.Lock()
    p._spawn_lock = threading.Lock()
    p.error = None
    p._busy = True
    p._finished = None

    class Dead:
        def send(self, cmd):
            raise BrokenPipeError

    p._cmd = Dead()
    monkeypatch.setattr(p, "_close_process", lambda: None, raising=False)

    def spawn():
        raise RuntimeError("mpv exited during startup")

    monkeypatch.setattr(p, "_spawn", spawn, raising=False)
    p.stop()  # skip path: must not raise
    assert p.error == "Audio player unavailable"
    with pytest.raises(RuntimeError):
        p.play("https://example.test")  # engine catches this and backs off
