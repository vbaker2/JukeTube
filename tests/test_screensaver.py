import copy
import json
import time

from jukebox.config import DEFAULTS
from jukebox.player import SimulatedPlayer
from jukebox.sources.demo import DemoSource
from jukebox.ui.app import App
from jukebox.ui.screens import HomeScreen
from jukebox.models import Track
from jukebox.inputs import Action


def make_app(tmp_path):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["_path"] = str(tmp_path / "config.toml")
    return App(cfg, DemoSource(), SimulatedPlayer(), tmp_path / "cache")


def test_completed_genre_plays_persist_for_screensaver(tmp_path):
    app = make_app(tmp_path)
    try:
        song = Track("s1", "Song", artist="Artist", art_url="cover.jpg", source="demo",
                     genre="Britpop")
        app._record_completed_play(song)
        app._record_completed_play(song)
        saved = json.loads((tmp_path / "play-stats.json").read_text())
        assert saved["genres"] == {"Britpop": 2}
        assert saved["tracks"][song.key]["plays"] == 2
        assert app._screensaver_items()[0]["genre"] == "Britpop"
    finally:
        app.shutdown()


def test_screensaver_starts_after_idle_even_without_music(tmp_path):
    app = make_app(tmp_path)
    try:
        app.push(HomeScreen(app))
        app.last_input = time.monotonic() - app.attract_after - 1
        app.update()
        assert app.attract
        assert app.screensaver_started is not None
    finally:
        app.shutdown()


def test_idle_screensaver_uses_full_display_instead_of_controls_panel(tmp_path):
    import pygame

    app = make_app(tmp_path)
    try:
        pygame.init()
        app.screen = pygame.Surface(app.size)
        app.attract = True
        app.controls_visible = True
        calls = []
        app.draw_attract = lambda *_: calls.append("screensaver")
        app.draw_controls = lambda *_: calls.append("controls")
        app.draw(0)
        assert calls == ["screensaver"]
    finally:
        app.shutdown()
        pygame.quit()


def test_playback_shortcuts_work_from_the_full_screen_visualiser(tmp_path):
    app = make_app(tmp_path)
    try:
        app.push(HomeScreen(app))
        app.attract = True
        skipped = []
        app.engine.skip = lambda: skipped.append(True) or True
        app.dispatch(Action.SKIP)
        assert skipped == [True]
        assert app.attract is False

        before = app.player.volume
        app.attract = True
        app.dispatch(Action.VOLUME_UP)
        assert app.player.volume == before + app.volume_step
        assert app.attract is False
    finally:
        app.shutdown()


def test_home_screen_starts_with_popular_dance(tmp_path):
    app = make_app(tmp_path)
    try:
        assert HomeScreen(app)._fixed()[0].title == "Popular Dance"
    finally:
        app.shutdown()


def test_control_diagram_marks_right_hand_volume_chords(tmp_path):
    app = make_app(tmp_path)
    try:
        labels = app.control_labels()
        assert labels["right_upper"] == "VOL +"
        assert labels["right_middle"] == "VOL +/-"
        assert labels["right_lower"] == "VOL −"
    finally:
        app.shutdown()
