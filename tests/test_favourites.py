from dataclasses import replace

import pytest

from jukebox.favourites import Favourites
from jukebox.models import Track


def test_favourites_persist_and_are_source_specific(tmp_path):
    path = tmp_path / "settings" / "favourites.json"
    saved = Favourites(path)
    track = Track("1", "Жизнь õ š ž", artist="Artist", source="ytmusic")
    demo = replace(track, source="demo")
    assert saved.toggle(track)
    assert saved.toggle(demo)
    reloaded = Favourites(path)
    assert reloaded.tracks("ytmusic") == [track]
    assert reloaded.tracks("demo") == [demo]
    assert not reloaded.toggle(replace(track, title="Updated title"))
    assert Favourites(path).tracks("ytmusic") == []
    assert Favourites(path).contains(demo)


@pytest.mark.parametrize("contents", ['broken json', '{}', '[{"title": "Missing id"}]',
                                     '[{"id": 1, "title": "Wrong type"}]'])
def test_invalid_file_is_preserved(tmp_path, contents):
    path = tmp_path / "favourites.json"
    path.write_text(contents)
    saved = Favourites(path)
    assert saved.error
    with pytest.raises(OSError):
        saved.toggle(Track("1", "Song"))
    assert path.read_text() == contents


def test_failed_save_keeps_previous_favourites(tmp_path, monkeypatch):
    path = tmp_path / "favourites.json"
    saved = Favourites(path)
    first, second = Track("1", "First"), Track("2", "Second")
    saved.toggle(first)

    def fail(*args):
        raise PermissionError("Read only")

    monkeypatch.setattr(type(path), "replace", fail)
    with pytest.raises(PermissionError):
        saved.toggle(second)
    assert saved.contains(first) and not saved.contains(second)
    assert Favourites(path).contains(first)
    assert list(tmp_path.iterdir()) == [path]


def test_favourites_menu_add_queue_remove_and_reload(tmp_path):
    import pygame

    from jukebox.config import load_config
    from jukebox.inputs import Action
    from jukebox.player import SimulatedPlayer
    from jukebox.sources.demo import DemoSource
    from jukebox.ui.app import App
    from jukebox.ui.screens import FavouritesScreen, ListScreen, QueueScreen

    pygame.init()
    cfg = load_config()
    cfg["_path"] = str(tmp_path / "config.toml")
    app = App(cfg, DemoSource(), SimulatedPlayer(), tmp_path / "cache")
    track = Track("1", "Test song", source="demo")
    try:
        listing = ListScreen(app, "Songs", rows=[track])
        listing.handle(Action.ALT)
        assert app.favourites.contains(track)
        assert "unfavourite" in listing.hint
        favourites = FavouritesScreen(app)
        assert favourites.rows == [track]
        favourites.handle(Action.SELECT)
        assert app.engine.count_in_queue(track) == 1
        queue = QueueScreen(app)
        queue.update()
        queue.handle(Action.ALT)
        favourites.update()
        assert favourites.rows == []
        assert Favourites(tmp_path / "favourites.json").tracks("demo") == []
    finally:
        app.shutdown()
        pygame.quit()
