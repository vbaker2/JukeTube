import copy
import json

from jukebox.config import DEFAULTS
from jukebox.inputs import Action
from jukebox.models import Collection
from jukebox.player import SimulatedPlayer
from jukebox.sources.demo import DemoSource
from jukebox.ui.app import App
from jukebox.ui.screens import HomeScreen, ListScreen, PlaylistChoiceScreen, ReorderScreen


def make_app(tmp_path):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["_path"] = str(tmp_path / "config.toml")
    return App(cfg, DemoSource(), SimulatedPlayer(), tmp_path / "cache")


def test_home_order_places_saved_rows_first_and_keeps_new_rows(tmp_path):
    app = make_app(tmp_path)
    try:
        app.home_order = ["Arcade Classics", "Settings"]
        rows = HomeScreen(app)._load()
        assert [row.title for row in rows[:3]] == ["Popular Dance", "Arcade Classics", "Settings"]
        assert {row.title for row in rows} >= {"Search", "Favourites", "Genres"}
    finally:
        app.shutdown()


def test_saved_home_order_can_reposition_popular_dance(tmp_path):
    app = make_app(tmp_path)
    try:
        app.home_order = ["Search", "Favourites", "Popular Dance"]
        rows = HomeScreen(app)._load()
        assert [row.title for row in rows[:3]] == ["Search", "Favourites", "Popular Dance"]
    finally:
        app.shutdown()


def test_reorder_screen_moves_and_persists_home_order(tmp_path):
    app = make_app(tmp_path)
    try:
        screen = ReorderScreen(app, "home")
        screen.items = ["Search", "Settings", "Genres"]
        screen.index = 1
        assert screen.handle(Action.LEFT)
        assert screen.items == ["Settings", "Search", "Genres"]
        assert screen.handle(Action.SELECT)
        saved = json.loads((tmp_path / "ui-order.json").read_text())
        assert saved["home"] == screen.items
    finally:
        app.shutdown()


def test_selecting_playlist_offers_queue_whole_or_browse(tmp_path):
    app = make_app(tmp_path)
    try:
        home = HomeScreen(app)
        app.push(home)
        playlist = Collection("playlist", "Family Mix", "mix1", source="demo")
        home.activate(playlist)
        choice = app.stack[-1]
        assert isinstance(choice, PlaylistChoiceScreen)
        assert [row.title for row in choice.rows] == ["Queue whole playlist", "Browse songs"]
        choice.activate(choice.rows[1])
        assert isinstance(app.stack[-1], ListScreen)
    finally:
        app.shutdown()
