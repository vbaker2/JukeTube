"""Navigable screens. Each screen draws into the main content area and reacts
to abstract Actions; the App owns the stack, header, now-playing bar etc."""

from __future__ import annotations

import math
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import pygame

from ..inputs import Action
from ..models import Collection, Item, Track, format_duration
from .theme import glow_rect, placeholder_art, pulse
from .controls import draw_labelled_layout

if TYPE_CHECKING:
    from .app import App

ROW_H = 66
THUMB = 52


@dataclass
class MenuEntry:
    """A synthetic row that runs a callback (used on the home screen)."""

    title: str
    subtitle: str
    action: Callable[[], None]
    icon: str = ""


Row = Item | MenuEntry


class Screen:
    title = ""
    hint = ""

    def __init__(self, app: App):
        self.app = app

    def on_enter(self) -> None: ...
    def on_exit(self) -> None: ...

    def handle(self, action: Action) -> bool:
        """Return True if the action was consumed."""
        return False

    def update(self) -> None: ...

    def draw(self, surf: pygame.Surface, rect: pygame.Rect, t: float) -> None: ...


class ListScreen(Screen):
    """A scrollable list of tracks/collections loaded in the background."""

    def __init__(self, app: App, title: str, loader: Callable[[], list[Row]] | None = None,
                 rows: list[Row] | None = None, empty_text: str = "Nothing here"):
        super().__init__(app)
        self.title = title
        self.loader = loader
        self.rows: list[Row] = rows or []
        self.empty_text = empty_text
        self.index = 0
        self.scroll = 0.0  # animated scroll offset in rows
        self.future: Future | None = None
        self.error: str | None = None
        if rows is None and loader is not None:
            self.reload()

    @property
    def loading(self) -> bool:
        return self.future is not None

    def reload(self) -> None:
        self.error = None
        self.future = self.app.submit(self.loader)

    def update(self) -> None:
        if self.future is not None and self.future.done():
            fut, self.future = self.future, None
            try:
                self.rows = list(fut.result())
                self.index = min(self.index, max(0, len(self.rows) - 1))
            except Exception as exc:
                self.error = str(exc) or exc.__class__.__name__

    @property
    def selected(self) -> Row | None:
        return self.rows[self.index] if 0 <= self.index < len(self.rows) else None

    @property
    def hint(self) -> str:  # type: ignore[override]
        sel = self.selected
        if isinstance(sel, Track):
            return "SELECT pick song   " + self.favourite_hint
        if isinstance(sel, Collection) and sel.kind in ("playlist", "album"):
            return "SELECT open   ALT queue all"
        return "SELECT open"

    @property
    def favourite_hint(self) -> str:
        if isinstance(self.selected, Track):
            return ("ALT unfavourite" if self.app.favourites.contains(self.selected)
                    else "ALT favourite")
        return ""

    def handle(self, action: Action) -> bool:
        if self.error and action == Action.SELECT and self.loader:
            self.reload()
            return True
        n = len(self.rows)
        page = max(1, self._visible - 1)
        if action == Action.UP and n:
            self.index = (self.index - 1) % n
        elif action == Action.DOWN and n:
            self.index = (self.index + 1) % n
        elif action == Action.LEFT and n:
            self.index = max(0, self.index - page)
        elif action == Action.RIGHT and n:
            self.index = min(n - 1, self.index + page)
        elif action == Action.SELECT:
            self.activate(self.selected)
        elif action == Action.ALT:
            self.alt(self.selected)
        else:
            return False
        return True

    def activate(self, row: Row | None) -> None:
        if isinstance(row, MenuEntry):
            row.action()
        elif isinstance(row, Track):
            self.app.request(row)
        elif isinstance(row, Collection):
            if row.kind == "playlist":
                self.app.push(PlaylistChoiceScreen(self.app, row))
                return
            src = self.app.source
            self.app.push(ListScreen(self.app, row.title, lambda: src.expand(row)))

    def alt(self, row: Row | None) -> None:
        if isinstance(row, Track):
            self.app.toggle_favourite(row)
        elif isinstance(row, Collection) and row.kind in ("playlist", "album"):
            self.app.queue_collection(row)


    _visible = 7

    def draw(self, surf: pygame.Surface, rect: pygame.Rect, t: float) -> None:
        th = self.app.theme
        c = th.c
        self._visible = max(1, rect.h // ROW_H)
        if self.loading and not self.rows:
            _spinner(surf, rect.center, t, c["accent2"])
            msg = th.text("Loading…", 22, c["muted"])
            surf.blit(msg, msg.get_rect(midtop=(rect.centerx, rect.centery + 40)))
            return
        if self.error:
            for i, (s, col) in enumerate(((self.error, c["warn"]), ("Press SELECT to retry", c["muted"]))):
                img = th.text(s, 24 if i == 0 else 20, col, max_width=rect.w - 40)
                surf.blit(img, img.get_rect(center=(rect.centerx, rect.centery + i * 36)))
            return
        if not self.rows:
            img = th.text(self.empty_text, 24, c["muted"])
            surf.blit(img, img.get_rect(center=rect.center))
            return

        # Keep the cursor away from the edges; ease the scroll toward target.
        target = min(max(0, self.index - self._visible // 2), max(0, len(self.rows) - self._visible))
        self.scroll += (target - self.scroll) * 0.35
        if abs(target - self.scroll) < 0.01:
            self.scroll = target
        first = int(self.scroll)
        offset = (self.scroll - first) * ROW_H
        prev_clip = surf.get_clip()
        surf.set_clip(rect.inflate(28, 20))  # leave room for the selection glow
        for i in range(first, min(len(self.rows), first + self._visible + 2)):
            y = rect.y + int((i - first) * ROW_H - offset)
            self._draw_row(surf, pygame.Rect(rect.x, y, rect.w - 14, ROW_H - 6), self.rows[i],
                           i == self.index, t)
        surf.set_clip(prev_clip)
        _scrollbar(surf, rect, self.scroll, self._visible, len(self.rows), c)

    def _draw_row(self, surf: pygame.Surface, r: pygame.Rect, row: Row, sel: bool, t: float) -> None:
        th = self.app.theme
        c = th.c
        if sel:
            glow_rect(surf, r, c["accent"], radius=10, spread=10, strength=int(60 + 50 * pulse(t, 1.2)))
            pygame.draw.rect(surf, c["panel_hi"], r, border_radius=10)
            pygame.draw.rect(surf, c["accent"], r, 2, border_radius=10)
        else:
            pygame.draw.rect(surf, c["panel"], r, border_radius=10)

        x = r.x + 8
        art_url = getattr(row, "art_url", None)
        thumb = self.app.art.get(art_url, THUMB) if art_url else None
        if thumb is None:
            if isinstance(row, MenuEntry) or (isinstance(row, Collection) and not art_url):
                thumb = _icon_tile(th, THUMB, row)
            else:
                thumb = placeholder_art(THUMB, getattr(row, "title", ""), th)
        surf.blit(thumb, (x, r.y + (r.h - THUMB) // 2))
        x += THUMB + 14

        right_w = 0
        if isinstance(row, Track):
            right = format_duration(row.duration) if row.duration else ""
            queued = self.app.engine.count_in_queue(row)
            if queued:
                right = "QUEUED  " + right
            if self.app.favourites.contains(row):
                right = "FAV  " + right
            if right:
                img = th.text(right, 18, c["good"] if queued else c["muted"], bold=bool(queued))
                surf.blit(img, img.get_rect(midright=(r.right - 14, r.centery)))
                right_w = img.get_width() + 20
        elif isinstance(row, (Collection, MenuEntry)):
            img = th.text("›", 34, c["accent2"] if sel else c["muted"], bold=True)
            surf.blit(img, img.get_rect(midright=(r.right - 14, r.centery - 2)))
            right_w = 34

        title, sub = _row_text(row)
        avail = r.right - x - right_w - 10
        t1 = th.text(title, 24, c["text"], bold=sel, max_width=avail)
        surf.blit(t1, (x, r.y + 7))
        if sub:
            t2 = th.text(sub, 17, c["accent2"] if sel else c["muted"], max_width=avail)
            surf.blit(t2, (x, r.y + 36))


class PlaylistChoiceScreen(ListScreen):
    """Make whole-playlist queueing an explicit SELECT option."""

    @property
    def hint(self) -> str:  # type: ignore[override]
        return "SELECT choose   BACK return"

    def __init__(self, app: App, playlist: Collection):
        self.playlist = playlist
        rows = [
            MenuEntry("Queue whole playlist", "Add as many songs as credits and queue space allow",
                      self.queue_playlist, "▶"),
            MenuEntry("Browse songs", "Open the playlist track list",
                      self.browse_playlist, "♪"),
        ]
        super().__init__(app, playlist.title, rows=rows)

    def queue_playlist(self) -> None:
        self.app.queue_collection(self.playlist)
        self.app.pop()

    def browse_playlist(self) -> None:
        source = self.app.source
        playlist = self.playlist
        self.app.push(ListScreen(self.app, playlist.title, lambda: source.expand(playlist)))


def _row_text(row: Row) -> tuple[str, str]:
    if isinstance(row, Track):
        sub = row.artist + (f" · {row.album}" if row.album else "")
        return row.title, ("[E] " if row.explicit else "") + sub
    if isinstance(row, Collection):
        return row.title, row.subtitle or row.kind.title()
    return row.title, row.subtitle


def _icon_tile(th, size: int, row) -> pygame.Surface:
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.rect(surf, th.c["panel_hi"], surf.get_rect(), border_radius=8)
    glyph = getattr(row, "icon", "") or (row.title[:1].upper() if row.title else "?")
    img = th.text(glyph, 30, th.c["accent2"], bold=True, title=True)
    surf.blit(img, img.get_rect(center=(size // 2, size // 2)))
    return surf


def _spinner(surf, center, t, color) -> None:
    for i in range(8):
        a = t * 6 + i * 0.785
        x = center[0] + math.cos(a) * 26
        y = center[1] + math.sin(a) * 26
        pygame.draw.circle(surf, tuple(int(v * (0.3 + 0.7 * i / 8)) for v in color), (int(x), int(y)), 5)


def _scrollbar(surf, rect, scroll, visible, total, c) -> None:
    if total <= visible:
        return
    track = pygame.Rect(rect.right - 6, rect.y, 4, rect.h)
    pygame.draw.rect(surf, c["panel"], track, border_radius=2)
    h = max(24, int(rect.h * visible / total))
    y = rect.y + int((rect.h - h) * scroll / max(1, total - visible))
    pygame.draw.rect(surf, c["accent2"], (track.x, y, 4, h), border_radius=2)


class HomeScreen(ListScreen):
    def __init__(self, app: App):
        super().__init__(app, app.source.display_name, loader=self._load)
        self.rows = self._fixed()

    def _fixed(self) -> list[Row]:
        app = self.app
        return [
            MenuEntry("Popular Dance", "Random UK dance and club favourites",
                      lambda: app.queue_popular_dance(), "♫"),
            MenuEntry("Search", "Find any song, artist or album", lambda: app.open_search(), "?"),
            MenuEntry("Up Next", "See what's playing and queued", lambda: app.push(QueueScreen(app)), "#"),
            MenuEntry("Favourites", "Your saved songs", lambda: app.push(FavouritesScreen(app)), "F"),
            MenuEntry("Settings", "Volume, profiles and cabinet controls",
                      lambda: app.push(ConfigScreen(app)), "⚙"),
        ]

    def _load(self) -> list[Row]:
        app = self.app
        rows = self._fixed()
        shelves = app.source.home()
        app.home_art_candidates = [
            {"id": getattr(item, "id", ""), "title": getattr(item, "title", ""),
             "artist": getattr(item, "artist", ""), "genre": getattr(item, "genre", ""),
             "art_url": item.art_url, "source": getattr(item, "source", ""), "plays": 0}
            for shelf in shelves for item in shelf.items
            if getattr(item, "art_url", None)
        ]
        for shelf in shelves:
            if shelf.title == "Browse":
                rows.extend(shelf.items)
            elif shelf.items:
                items = list(shelf.items)
                rows.append(MenuEntry(
                    shelf.title, f"{len(items)} picks",
                    lambda title=shelf.title, items=items: app.push(ListScreen(app, title, rows=items)),
                ))
        order = {name: i for i, name in enumerate(app.home_order)}
        # Make the one-touch dance mix visible on the first page even when an
        # older saved menu order predates this entry; explicit reordering can
        # still place it wherever the owner wants.
        return [row for _, row in sorted(enumerate(rows),
                                         key=lambda pair: (
                                             order.get(pair[1].title,
                                                       -1 if pair[1].title == "Popular Dance" else len(order)),
                                             pair[0]))]

    def update(self) -> None:
        super().update()
        if self.error:
            # Even offline, Search and Up Next stay usable; retry later.
            self.app.toast(f"Couldn't load {self.app.source.display_name}: {self.error}")
            self.error = None
            self.rows = self._fixed()


class PlaybackScreen(ListScreen):
    def __init__(self, app: App):
        items = [("Skip song", Action.SKIP), ("Pause / resume", Action.PAUSE),
                 ("Volume down", Action.VOLUME_DOWN), ("Volume up", Action.VOLUME_UP),
                 ("Exit jukebox", Action.EXIT)]
        super().__init__(app, "Playback controls", rows=[
            MenuEntry(title, "Press SELECT", lambda action=action: app.dispatch(action))
            for title, action in items if action != Action.SKIP or app.engine.cfg.allow_skip
        ])


class ConfigScreen(ListScreen):
    """Cabinet and account settings, kept off the jukebox's main screen."""

    def __init__(self, app: App):
        rows = [
            MenuEntry(f"Volume −  {app.player.volume}%", "Turn the music down",
                      lambda: app.change_volume(-app.volume_step), "−"),
            MenuEntry(f"Volume +  {app.player.volume}%", "Turn the music up",
                      lambda: app.change_volume(app.volume_step), "+"),
            MenuEntry("Family profiles" if app.profiles else "Set up family profiles",
                      f"Browsing as {app.profile_name}" if app.profiles
                      else "Add each person's YouTube Music auth file",
                      lambda: app.push(ProfileScreen(app) if app.profiles else ProfilesSetupScreen(app)), "P"),
            MenuEntry("Controls", "Joystick and button guide", lambda: app.push(ControlsScreen(app)), "i"),
            MenuEntry("Show / hide controls", "Toggle the always-visible guide", app.toggle_controls, "C"),
            MenuEntry("Playback controls", "Skip, pause, volume and quit",
                      lambda: app.push(PlaybackScreen(app)), "▶"),
            MenuEntry("Arrange home menu", "Move items with LEFT / RIGHT",
                      lambda: app.push(ReorderScreen(app, "home")), "↕"),
            MenuEntry("Arrange genres", "Choose the order of the genre list",
                      lambda: app.push(ReorderScreen(app, "genres")), "♫"),
        ]
        super().__init__(app, "Settings", rows=rows)


class ReorderScreen(Screen):
    """Reorder home rows or genres using the cabinet directions."""

    hint = "UP/DOWN select   LEFT/RIGHT move   SELECT save   BACK cancel"

    def __init__(self, app: App, kind: str):
        super().__init__(app)
        self.kind = kind
        if kind == "genres":
            self.title = "Arrange genres"
            source_genres = getattr(app.source, "genres", [])
            self.items = [g["name"] for g in source_genres if isinstance(g, dict) and g.get("name")]
        else:
            self.title = "Arrange home menu"
            # Reuse the live home screen: constructing a new HomeScreen would
            # start a network load and overwrite app.home_art_candidates.
            home = app.stack[0] if app.stack and isinstance(app.stack[0], HomeScreen) else None
            self.items = [row.title for row in home._fixed()] if home else []
            # Use saved order first; current non-fixed rows arrive asynchronously.
            self.items = list(dict.fromkeys(app.home_order + self.items))
            if app.stack and isinstance(app.stack[0], HomeScreen):
                self.items = list(dict.fromkeys([getattr(row, "title", "")
                                                for row in app.stack[0].rows if getattr(row, "title", "")]
                                                + self.items))
        self.index = 0
        self.scroll = 0.0
        self.visible = 7

    def handle(self, action: Action) -> bool:
        if action == Action.UP and self.items:
            self.index = (self.index - 1) % len(self.items)
        elif action == Action.DOWN and self.items:
            self.index = (self.index + 1) % len(self.items)
        elif action in (Action.LEFT, Action.RIGHT) and self.items:
            target = self.index + (-1 if action == Action.LEFT else 1)
            if 0 <= target < len(self.items):
                self.items[self.index], self.items[target] = self.items[target], self.items[self.index]
                self.index = target
        elif action == Action.SELECT:
            if self.app.save_ui_order(self.kind, self.items):
                self.app.pop()
        elif action in (Action.BACK, Action.EXIT):
            self.app.pop()
        else:
            return False
        return True

    def draw(self, surf: pygame.Surface, rect: pygame.Rect, t: float) -> None:
        th, c = self.app.theme, self.app.theme.c
        self.visible = max(1, rect.h // ROW_H)
        if not self.items:
            msg = th.text("No genres available for this source", 22, c["muted"])
            surf.blit(msg, msg.get_rect(center=rect.center))
            return
        target = min(max(0, self.index - self.visible // 2), max(0, len(self.items) - self.visible))
        self.scroll += (target - self.scroll) * 0.35
        if abs(target - self.scroll) < 0.01:
            self.scroll = target
        first = int(self.scroll)
        offset = (self.scroll - first) * ROW_H
        for i in range(first, min(len(self.items), first + self.visible + 1)):
            y = rect.y + int((i - first) * ROW_H - offset)
            row = pygame.Rect(rect.x, y, rect.w - 14, ROW_H - 6)
            selected = i == self.index
            if selected:
                glow_rect(surf, row, c["accent"], radius=10, spread=10,
                          strength=int(60 + 50 * pulse(t, 1.2)))
            pygame.draw.rect(surf, c["panel_hi"] if selected else c["panel"], row, border_radius=10)
            if selected:
                pygame.draw.rect(surf, c["accent"], row, 2, border_radius=10)
            label = th.text(f"{i + 1:02d}   {self.items[i]}", 23,
                            c["text"] if selected else c["muted"], bold=selected,
                            max_width=row.w - 36)
            surf.blit(label, (row.x + 18, row.y + (row.h - label.get_height()) // 2))
        _scrollbar(surf, rect, self.scroll, self.visible, len(self.items), c)


class ProfileScreen(ListScreen):
    def __init__(self, app: App):
        rows = [MenuEntry(profile["name"], "Current profile" if profile.get("id") == app.profile_id
                          else "Switch YouTube Music library",
                          lambda profile=profile: self.choose(profile))
                for profile in app.profiles]
        super().__init__(app, "Profiles", rows=rows, empty_text="No profiles configured")

    def choose(self, profile: dict) -> None:
        if self.app.set_profile(profile):
            self.app.pop()


class ProfilesSetupScreen(Screen):
    title = "Family profiles"
    hint = "BACK return"

    def draw(self, surf: pygame.Surface, rect: pygame.Rect, t: float) -> None:
        th, c = self.app.theme, self.app.theme.c
        lines = [
            "Each child has a separate supervised Google Account.",
            "Create one auth file per account while signed in to",
            "that person's YouTube Music account, using:",
            ".venv/bin/ytmusicapi browser --file <name>.json",
            "Add one profile entry per account to config.toml; see README.",
            "Keep auth files private; never share them in chat.",
        ]
        y = rect.y + 36
        for i, line in enumerate(lines):
            img = th.text(line, 24 if i == 0 else 20, c["text"] if i == 0 else c["muted"],
                          max_width=rect.w - 36)
            surf.blit(img, (rect.x + 18, y))
            y += 46


class ControlsScreen(Screen):
    title = "Controls"
    hint = "BACK return"

    def draw(self, surf: pygame.Surface, rect: pygame.Rect, t: float) -> None:
        th, c = self.app.theme, self.app.theme.c
        diagram = pygame.Rect(rect.x, rect.y, rect.w, max(1, rect.h - 64))
        fitted = pygame.Rect(0, 0, 720, 480).fit(diagram)
        draw_labelled_layout(surf, fitted, th, self.app.control_labels())
        note = ("Select picks a song · Start opens settings · Favourite saves a song"
                if self.app.button_positions else "Calibration needed to identify the physical buttons")
        surf.blit(th.text(note, 21, c["text"], max_width=rect.w), (rect.x, rect.bottom - 55))
        hint = f"QUIT: hold Coin + Start together for {self.app.inputs.exit_hold:g} second(s)"
        surf.blit(th.text(hint, 22, c["warn"], max_width=rect.w), (rect.x, rect.bottom - 28))


class FavouritesScreen(ListScreen):
    def __init__(self, app: App):
        super().__init__(app, "Favourites", rows=[],
                         empty_text="No favourites yet - highlight a song and press ALT")
        self.update()

    def update(self) -> None:
        self.rows = self.app.favourites.tracks(self.app.source.name)
        self.index = min(self.index, max(0, len(self.rows) - 1))
        if self.app.favourites.error:
            self.empty_text = "Couldn't read favourites - check favourites.json"

    def alt(self, row: Row | None) -> None:
        super().alt(row)
        self.update()


class QueueScreen(ListScreen):
    title = "Up Next"

    def __init__(self, app: App):
        super().__init__(app, "Up Next", rows=[], empty_text="Queue is empty - pick a song!")

    def update(self) -> None:
        eng = self.app.engine
        rows: list[Row] = []
        if eng.now_playing:
            rows.append(eng.now_playing.track)
        rows.extend(e.track for e in eng.up_next)
        self.rows = rows
        self.index = min(self.index, max(0, len(rows) - 1))

    @property
    def hint(self) -> str:  # type: ignore[override]
        skip = "SKIP skip current   " if self.app.engine.cfg.allow_skip else ""
        return skip + self.favourite_hint

    def activate(self, row) -> None:
        pass

    def _draw_row(self, surf, r, row, sel, t) -> None:
        super()._draw_row(surf, r, row, sel, t)
        eng = self.app.engine
        th = self.app.theme
        idx = self.rows.index(row) if row in self.rows else -1
        if idx == 0 and eng.now_playing:
            label, col = "NOW PLAYING", th.c["accent"]
        else:
            q = idx - (1 if eng.now_playing else 0)
            entry = eng.up_next[q] if 0 <= q < len(eng.up_next) else None
            label = f"#{q + 1}" if entry and entry.requested else "AUTOPLAY"
            col = th.c["accent2"] if entry and entry.requested else th.c["muted"]
        img = th.text(label, 14, col, bold=True)
        surf.blit(img, img.get_rect(topright=(r.right - 14, r.y + 6)))


KEY_ROWS = [
    list("ABCDEFGHIJ"),
    list("KLMNOPQRST"),
    list("UVWXYZ0123"),
    list("456789&'-."),
    ["SPACE", "DEL", "CLEAR", "SEARCH"],
]


class SearchScreen(Screen):
    title = "Search"
    hint = "SELECT type   ALT space   START search"

    def __init__(self, app: App):
        super().__init__(app)
        self.row = 0
        self.col = 0

    def on_enter(self) -> None:
        self.app.inputs.typing = True
        pygame.key.start_text_input()

    def on_exit(self) -> None:
        self.app.inputs.typing = False
        pygame.key.stop_text_input()

    # The query lives on the App so it survives leaving and re-entering.
    @property
    def query(self) -> str:
        return self.app.last_query

    @query.setter
    def query(self, value: str) -> None:
        self.app.last_query = value[:60]

    def update(self) -> None:
        for ch in self.app.inputs.take_text():
            self.query = self.query[:-1] if ch == "\b" else self.query + ch

    def _press(self, key: str) -> None:
        if key == "SPACE":
            self.query += " "
        elif key == "DEL":
            self.query = self.query[:-1]
        elif key == "CLEAR":
            self.query = ""
        elif key == "SEARCH":
            self.submit()
        else:
            self.query += key

    def submit(self) -> None:
        q = self.query.strip()
        if not q:
            return
        src = self.app.source
        self.app.push(ListScreen(self.app, f'"{q}"', lambda: src.search(q),
                                 empty_text="No results - try another search"))

    def handle(self, action: Action) -> bool:
        if action == Action.UP:
            self.row = (self.row - 1) % len(KEY_ROWS)
        elif action == Action.DOWN:
            self.row = (self.row + 1) % len(KEY_ROWS)
        elif action == Action.LEFT:
            self.col = (self.col - 1) % len(KEY_ROWS[self.row])
        elif action == Action.RIGHT:
            self.col = (self.col + 1) % len(KEY_ROWS[self.row])
        elif action == Action.SELECT:
            self.col = min(self.col, len(KEY_ROWS[self.row]) - 1)
            self._press(KEY_ROWS[self.row][self.col])
        elif action == Action.ALT:
            self._press("SPACE")
        elif action == Action.START:
            self.submit()
        else:
            return False
        if action in (Action.UP, Action.DOWN):
            self.col = min(self.col, len(KEY_ROWS[self.row]) - 1)
        return True

    def draw(self, surf: pygame.Surface, rect: pygame.Rect, t: float) -> None:
        th = self.app.theme
        c = th.c
        box = pygame.Rect(rect.x + 20, rect.y + 6, rect.w - 40, 64)
        glow_rect(surf, box, c["accent2"], spread=10, strength=70)
        pygame.draw.rect(surf, c["panel"], box, border_radius=12)
        pygame.draw.rect(surf, c["accent2"], box, 2, border_radius=12)
        shown = self.query or ""
        img = th.text(shown or "Type an artist, song or album", 32,
                      c["text"] if shown else c["muted"], max_width=box.w - 50)
        surf.blit(img, (box.x + 20, box.centery - img.get_height() // 2))
        if shown and pulse(t, 2) > 0.5:
            cx = box.x + 22 + img.get_width()
            pygame.draw.rect(surf, c["accent"], (cx, box.y + 14, 3, box.h - 28))

        top = box.bottom + 24
        gap = 8
        key_h = (rect.bottom - top - gap * (len(KEY_ROWS) - 1)) // len(KEY_ROWS)
        key_h = min(key_h, 70)
        for ri, row in enumerate(KEY_ROWS):
            n = len(row)
            kw = (rect.w - 40 - gap * (n - 1)) // n
            for ci, key in enumerate(row):
                kr = pygame.Rect(rect.x + 20 + ci * (kw + gap), top + ri * (key_h + gap), kw, key_h)
                sel = ri == self.row and ci == min(self.col, n - 1)
                if sel:
                    glow_rect(surf, kr, c["accent"], spread=8, strength=int(70 + 60 * pulse(t, 1.5)))
                    pygame.draw.rect(surf, c["accent"], kr, border_radius=10)
                    col = (20, 10, 30)
                else:
                    pygame.draw.rect(surf, c["panel"], kr, border_radius=10)
                    pygame.draw.rect(surf, c["panel_hi"], kr, 2, border_radius=10)
                    col = c["accent2"] if key == "SEARCH" else c["text"]
                img = th.text(key, 28 if len(key) == 1 else 22, col, bold=True)
                surf.blit(img, img.get_rect(center=kr.center))
