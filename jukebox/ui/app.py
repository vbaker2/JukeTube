"""The pygame application: window, main loop and the persistent chrome
(header, Up Next panel, now-playing bar, toasts, attract mode)."""

from __future__ import annotations

import logging
import json
import math
import os
import tempfile
import time
from pathlib import Path
from concurrent.futures import Future, ThreadPoolExecutor

import pygame

from ..engine import EngineConfig, Jukebox
from ..config import default_config_path
from ..favourites import Favourites
from ..inputs import Action, InputMapper
from ..models import Collection, Track, format_duration
from ..player import Player
from ..sources import Source, create_source
from ..updater import UpdateAgent
from .art import ArtCache
from .controls import draw_labelled_layout
from .screens import ConfigScreen, HomeScreen, ListScreen, Screen, SearchScreen
from .theme import Theme, glow_rect, placeholder_art, pulse

log = logging.getLogger(__name__)

HEADER_H = 72
BAR_H = 112
LEGEND_H = 30
PANEL_W = 360
MARGIN = 24


class App:
    def __init__(self, cfg: dict, source: Source, player: Player, cache_dir, player_warning=None,
                 screenshot: str | None = None, max_frames: int | None = None):
        self.cfg = cfg
        self.profiles = list((cfg.get("source", {}).get("ytmusic") or {}).get("profiles") or [])
        self.profile_id = cfg.get("_profile_id", "")
        selected_profile = next((p for p in self.profiles if p.get("id") == self.profile_id), None)
        self.profile_name = selected_profile.get("name", "YouTube Music") if selected_profile else "YouTube Music"
        disp = cfg["display"]
        self.size = (int(disp["width"]), int(disp["height"]))
        self.fps = int(disp["fps"])
        self.attract_after = float(disp["attract_after"])
        self.volume_step = int(cfg["audio"]["volume_step"])
        self.source = source
        self._order_path = Path(cfg.get("_path", default_config_path())).with_name("ui-order.json")
        self.home_order: list[str] = []
        self.genre_order: list[str] = []
        try:
            order = json.loads(self._order_path.read_text(encoding="utf-8"))
            if isinstance(order, dict):
                self.home_order = [str(x) for x in order.get("home", []) if isinstance(x, str)]
                self.genre_order = [str(x) for x in order.get("genres", []) if isinstance(x, str)]
        except (OSError, ValueError):
            pass
        self._apply_genre_order()
        self.player = player
        self._pool = ThreadPoolExecutor(4, thread_name_prefix="net")
        self.engine = Jukebox(source, player, EngineConfig(**cfg["jukebox"]), submit=self.submit)
        self.updater = None
        if os.environ.get("JUKEBOX_AUTO_UPDATE") == "1":
            self.updater = UpdateAgent(Path(__file__).resolve().parents[2],
                                       Path.home() / ".local/state/jukebox")
            self.updater.restore_queue(self.engine)
            self.updater.start()
        self.theme = Theme(disp.get("theme", "neon"))
        self.art = ArtCache(cache_dir)
        self._play_stats_path = Path(cfg.get("_path", default_config_path())).with_name("play-stats.json")
        self.play_stats: dict = {"genres": {}, "tracks": {}}
        self._play_stats_error = False
        try:
            saved_stats = json.loads(self._play_stats_path.read_text(encoding="utf-8"))
            if isinstance(saved_stats, dict):
                self.play_stats["genres"] = saved_stats.get("genres", {}) if isinstance(
                    saved_stats.get("genres", {}), dict) else {}
                self.play_stats["tracks"] = saved_stats.get("tracks", {}) if isinstance(
                    saved_stats.get("tracks", {}), dict) else {}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            self._play_stats_error = True
            log.warning("Couldn't read play stats; preserving the file: %s", exc)
        self.screensaver_started: float | None = None
        self.home_art_candidates: list[dict] = []
        self.inputs = InputMapper(cfg["controls"])
        self.stack: list[Screen] = []
        self.toasts: list[tuple[str, float]] = []
        self._controls_path = Path(cfg.get("_path", default_config_path())).with_name("controls-visible.json")
        self.button_positions = {}
        self.profile_button = None
        try:
            positions_path = self._controls_path.with_name("cabinet-buttons.json")
            positions = json.loads(positions_path.read_text(encoding="utf-8"))
            if isinstance(positions, dict):
                self.button_positions = {p: r for p, r in positions.items()
                                         if isinstance(r, dict) and isinstance(r.get("button"), int)}
                right_coin = self.button_positions.get("top_right")
                if right_coin and isinstance(right_coin.get("instance_id"), int):
                    self.profile_button = (right_coin["instance_id"], right_coin["button"])
                    self.inputs.set_device_button_action(*self.profile_button, Action.PROFILE_NEXT)
                right_buttons = [self.button_positions.get(pos) for pos in
                                 ("right_upper", "right_middle", "right_lower")]
                if all(isinstance(item, dict) and isinstance(item.get("instance_id"), int)
                       and isinstance(item.get("button"), int) for item in right_buttons):
                    upper, middle, lower = right_buttons
                    if upper["instance_id"] == middle["instance_id"]:
                        self.inputs.set_device_chord(upper["instance_id"],
                                                     (upper["button"], middle["button"]),
                                                     Action.VOLUME_UP)
                    if middle["instance_id"] == lower["instance_id"]:
                        self.inputs.set_device_chord(middle["instance_id"],
                                                     (middle["button"], lower["button"]),
                                                     Action.VOLUME_DOWN)
        except (OSError, ValueError):
            pass
        self.controls_visible = True
        try:
            saved = json.loads(self._controls_path.read_text(encoding="utf-8"))
            if isinstance(saved, bool):
                self.controls_visible = saved
        except (OSError, ValueError):
            pass
        self.favourites = Favourites(Path(cfg.get("_path", default_config_path())).with_name("favourites.json"))
        if self.favourites.error:
            self.toast("Couldn't read favourites; saved file has been kept", 8)
        self.last_input = time.monotonic()
        self.attract = False
        self.confirm_exit = False
        self.running = True
        self._pending_queue: list[Future] = []  # appended from worker threads
        self.last_query = ""
        self._screenshot = screenshot
        self._max_frames = max_frames
        if player_warning:
            self.toast(player_warning, 8)

    # ------------------------------------------------------------ helpers
    def submit(self, fn, *args) -> Future:
        return self._pool.submit(fn, *args)

    def _apply_genre_order(self) -> None:
        genres = getattr(self.source, "genres", None)
        if not isinstance(genres, list) or not self.genre_order:
            return
        order = {name: i for i, name in enumerate(self.genre_order)}
        genres.sort(key=lambda genre: order.get(genre.get("name", ""), len(order)))

    def save_ui_order(self, kind: str, names: list[str]) -> bool:
        if kind == "home":
            self.home_order = list(names)
        elif kind == "genres":
            self.genre_order = list(names)
            self._apply_genre_order()
        temporary = None
        try:
            self._order_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self._order_path.parent, delete=False,
                                             encoding="utf-8") as file:
                temporary = Path(file.name)
                json.dump({"home": self.home_order, "genres": self.genre_order}, file, indent=2)
                file.write("\n")
            temporary.replace(self._order_path)
        except OSError as exc:
            log.warning("Couldn't save jukebox menu order: %s", exc)
            self.toast("Couldn't save this order", 5)
            return False
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        if kind == "home" and self.stack and isinstance(self.stack[0], HomeScreen):
            self.stack[0].reload()
        if kind == "genres" and self.stack and isinstance(self.stack[0], HomeScreen):
            self.stack[0].reload()
        self.toast("Order saved")
        return True

    def toast(self, msg: str, seconds: float = 2.8) -> None:
        self.toasts.append((msg, time.monotonic() + seconds))
        self.toasts = self.toasts[-3:]

    def push(self, screen: Screen) -> None:
        if self.stack:
            self.stack[-1].on_exit()
        self.stack.append(screen)
        screen.on_enter()

    def pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop().on_exit()
            self.stack[-1].on_enter()

    def open_search(self) -> None:
        for i, s in enumerate(self.stack):
            if isinstance(s, SearchScreen):
                while len(self.stack) > i + 1:
                    self.pop()
                return
        self.push(SearchScreen(self))

    def request(self, track: Track) -> None:
        self.engine.request(track)

    def set_profile(self, profile: dict) -> bool:
        if profile.get("id") == self.profile_id:
            self.toast(f"Already browsing as {self.profile_name}")
            return True
        source_cfg = self.cfg["source"].get("ytmusic") or {}
        options = {key: value for key, value in source_cfg.items()
                   if key != "profiles" and value != ""}
        auth_file = profile.get("auth_file", options.get("auth_file", ""))
        options["auth_file"] = str(Path(auth_file).expanduser()) if auth_file else ""
        if profile.get("user"):
            options["user"] = str(profile["user"])
        try:
            source = create_source(self.cfg["source"]["name"], options)
        except Exception as exc:
            self.toast(f"Couldn't open {profile.get('name', 'profile')}: {exc}", 6)
            return False
        self.source = self.engine.source = source
        self._apply_genre_order()
        self.profile_id = str(profile.get("id", ""))
        self.profile_name = str(profile.get("name", "YouTube Music"))
        self.cfg["_profile_id"] = self.profile_id
        path = Path(self.cfg.get("_path", default_config_path())).with_name("profile.json")
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False,
                                             encoding="utf-8") as file:
                temporary = Path(file.name)
                json.dump({"id": self.profile_id}, file)
            temporary.replace(path)
        except OSError as exc:
            log.warning("Couldn't persist selected profile: %s", exc)
            self.toast("Profile switched; couldn't save it for next time", 5)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        if self.stack and isinstance(self.stack[0], HomeScreen):
            self.stack[0].reload()
        self.toast(f"Browsing as {self.profile_name}")
        return True

    def next_profile(self) -> None:
        if not self.profiles:
            self.toast("No profiles configured yet")
            return
        current = next((i for i, profile in enumerate(self.profiles)
                        if profile.get("id") == self.profile_id), -1)
        self.set_profile(self.profiles[(current + 1) % len(self.profiles)])

    def toggle_controls(self) -> None:
        visible = not self.controls_visible
        temporary = None
        try:
            self._controls_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self._controls_path.parent,
                                             delete=False, encoding="utf-8") as file:
                temporary = Path(file.name)
                json.dump(visible, file)
            temporary.replace(self._controls_path)
        except OSError:
            self.toast("Couldn't save the controls display preference")
            return
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        self.controls_visible = visible
        self.toast("Controls shown" if visible else "Controls hidden — use the home menu to show them")

    def toggle_favourite(self, track: Track) -> None:
        try:
            added = self.favourites.toggle(track)
        except OSError as exc:
            log.warning("Couldn't save favourites: %s", exc)
            self.toast("Couldn't save favourites; check the file and permissions", 6)
            return
        self.toast("Added to favourites" if added else "Removed from favourites")

    def change_volume(self, delta: int) -> None:
        volume = self.engine.change_volume(delta)
        self.toast(f"Volume {volume}", 1.8)

    def queue_collection(self, c: Collection) -> None:
        self.toast(f"Fetching {c.title}…")
        fut = self.submit(self.source.expand, c)

        def done(f: Future) -> None:
            self._pending_queue.append(f)

        fut.add_done_callback(done)

    def queue_popular_dance(self) -> None:
        self.toast("Finding popular dance tracks…")
        fut = self.submit(self.source.popular_dance)
        fut.add_done_callback(lambda completed: self._pending_queue.append(completed))

    def _record_completed_play(self, track: Track) -> None:
        """Persist completed plays for the idle cover gallery and genre ranking."""
        if self._play_stats_error:
            return
        tracks = self.play_stats["tracks"]
        record = tracks.setdefault(track.key, {
            "id": track.id, "title": track.title, "artist": track.artist,
            "album": track.album, "art_url": track.art_url, "source": track.source,
            "genre": track.genre, "plays": 0,
        })
        record.update({"title": track.title, "artist": track.artist, "album": track.album,
                       "art_url": track.art_url or record.get("art_url"),
                       "genre": track.genre or record.get("genre", "")})
        record["plays"] = int(record.get("plays", 0)) + 1
        if track.genre:
            genres = self.play_stats["genres"]
            genres[track.genre] = int(genres.get(track.genre, 0)) + 1
        temporary = None
        try:
            self._play_stats_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self._play_stats_path.parent,
                                             delete=False, encoding="utf-8") as file:
                temporary = Path(file.name)
                json.dump(self.play_stats, file, ensure_ascii=False, indent=2)
                file.write("\n")
            temporary.replace(self._play_stats_path)
        except OSError as exc:
            log.warning("Couldn't save play stats: %s", exc)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _screensaver_items(self) -> list[dict]:
        tracks = list(self.play_stats.get("tracks", {}).values())
        tracks.extend({"id": track.id, "title": track.title, "artist": track.artist,
                       "album": track.album, "art_url": track.art_url, "source": track.source,
                       "genre": track.genre, "plays": 0}
                      for track in self.favourites.tracks(self.source.name))
        tracks.extend(self.home_art_candidates)
        # Prefer unique covers, while keeping the most-played genres and songs first.
        genre_counts = self.play_stats.get("genres", {})
        tracks.sort(key=lambda item: (-int(genre_counts.get(item.get("genre", ""), 0)),
                                      -int(item.get("plays", 0)), item.get("title", "")))
        unique: list[dict] = []
        seen: set[str] = set()
        for item in tracks:
            key = item.get("art_url") or f"{item.get('source', '')}:{item.get('id', item.get('title', ''))}"
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
            if len(unique) >= 16:
                break
        return unique

    # --------------------------------------------------------------- loop
    def run(self) -> None:
        disp = self.cfg["display"]
        pygame.display.set_caption("JukeTube by Vince Baker")
        # SDL's SCALED renderer can enlarge with nearest-neighbour sampling on
        # the cabinet. Keep a logical canvas and explicitly smooth the final
        # presentation into the desktop, preserving its aspect ratio.
        fullscreen = disp["fullscreen"]
        window = pygame.display.set_mode((0, 0) if fullscreen else self.size,
                                         pygame.FULLSCREEN if fullscreen else 0)
        self.screen = pygame.Surface(self.size).convert()
        viewport = self.screen.get_rect().fit(window.get_rect())
        frame = pygame.Surface(viewport.size).convert()
        pygame.mouse.set_visible(not disp["hide_cursor"])
        pygame.key.stop_text_input()
        self.push(HomeScreen(self))
        clock = pygame.time.Clock()
        frames = 0
        start = time.monotonic()
        try:
            while self.running:
                now = time.monotonic()
                actions: list[Action] = []
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                    actions += self.inputs.handle(event, now)
                actions += self.inputs.repeats(now)
                for a in actions:
                    self.dispatch(a)
                self.update()
                self.draw(now - start)
                window.fill((0, 0, 0))
                if viewport.size == self.size:
                    window.blit(self.screen, viewport)
                else:
                    pygame.transform.smoothscale(self.screen, viewport.size, frame)
                    window.blit(frame, viewport)
                pygame.display.flip()
                frames += 1
                if self._max_frames and frames >= self._max_frames:
                    if self._screenshot:
                        pygame.image.save(self.screen, self._screenshot)
                    self.running = False
                clock.tick(self.fps)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self.updater:
            self.updater.stop()
        self.player.shutdown()
        self.art.shutdown()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def dispatch(self, action: Action) -> None:
        self.last_input = time.monotonic()
        if action == Action.QUIT:
            self.confirm_exit = True
            return
        eng = self.engine
        if self.attract:
            self.attract = False
            if action not in (Action.COIN, Action.PROFILE_NEXT, Action.SKIP,
                              Action.VOLUME_UP, Action.VOLUME_DOWN):
                return  # first press just wakes the screen
        if self.confirm_exit:
            if action == Action.SELECT:
                self.running = False
            elif action in (Action.BACK, Action.EXIT):
                self.confirm_exit = False
            return
        if action == Action.COIN:
            eng.insert_coin()
            return
        if action == Action.EXIT:
            self.confirm_exit = True
            return
        if action == Action.PROFILE_NEXT:
            self.next_profile()
            return
        # Playback controls must work from every screen, including screens
        # whose navigation handlers consume otherwise-unrecognised actions.
        if action == Action.SKIP:
            if not eng.skip():
                self.toast("Skipping is disabled" if eng.now_playing else "Nothing playing")
            return
        if action == Action.VOLUME_UP:
            self.toast(f"Volume {eng.change_volume(self.volume_step)}", 1.2)
            return
        if action == Action.VOLUME_DOWN:
            self.toast(f"Volume {eng.change_volume(-self.volume_step)}", 1.2)
            return
        if self.stack[-1].handle(action):
            return
        if action == Action.BACK:
            self.pop()
        elif action == Action.START:
            self.push(ConfigScreen(self))
        elif action == Action.PAUSE:
            eng.toggle_pause()

    def update(self) -> None:
        self.engine.tick(before_start=self.updater.hold_at_boundary if self.updater else None)
        if os.environ.get("JUKEBOX_UPDATE_RESTART") == "1":
            self.running = False
        while self.engine.completed:
            self._record_completed_play(self.engine.completed.popleft())
        while self.engine.messages:
            self.toast(self.engine.messages.popleft())
        pending, self._pending_queue = self._pending_queue, []
        for fut in pending:
            if not fut.done():
                self._pending_queue.append(fut)
                continue
            try:
                tracks = [i for i in fut.result() if isinstance(i, Track)]
                self.engine.request_many(tracks)
            except Exception as exc:
                self.toast(f"Couldn't queue: {exc}")
        self.stack[-1].update()
        now = time.monotonic()
        self.toasts = [(m, t) for m, t in self.toasts if t > now]
        if (not self.attract and self.attract_after > 0
                and now - self.last_input > self.attract_after):
            self.attract = True
            self.screensaver_started = now

    # --------------------------------------------------------------- draw
    def draw(self, t: float) -> None:
        surf = self.screen
        surf.blit(self.theme.background(self.size), (0, 0))
        if self.attract:
            if self.engine.now_playing is None:
                # The idle screensaver takes over the whole cabinet; controls
                # return as soon as an input wakes the normal menu.
                self.draw_attract(surf, t)
            elif self.controls_visible:
                preview = pygame.Surface(self.size)
                preview.blit(self.theme.background(self.size), (0, 0))
                self.draw_attract(preview, t)
                width = self.size[0] - PANEL_W - MARGIN * 2
                height = round(self.size[1] * width / self.size[0])
                surf.blit(pygame.transform.smoothscale(preview, (width, height)),
                          (0, (self.size[1] - height) // 2))
                self.draw_controls(surf, pygame.Rect(self.size[0] - PANEL_W - MARGIN,
                                                     118, PANEL_W, 436))
            else:
                self.draw_attract(surf, t)
        else:
            w, h = self.size
            self.draw_header(surf, t)
            content = pygame.Rect(MARGIN, HEADER_H + 46, w - PANEL_W - MARGIN * 3,
                                  h - HEADER_H - 46 - BAR_H - LEGEND_H - 18)
            self.draw_breadcrumb(surf, content)
            self.stack[-1].draw(surf, content, t)
            panel = pygame.Rect(content.right + MARGIN, HEADER_H + 12, PANEL_W, content.bottom - HEADER_H - 12)
            if self.controls_visible:
                guide = pygame.Rect(panel.x, panel.y + 130, panel.w, panel.h - 130)
                self.draw_up_next(surf, pygame.Rect(panel.x, panel.y, panel.w, 118), t)
                self.draw_controls(surf, guide)
            else:
                self.draw_up_next(surf, panel, t)
            self.draw_now_playing(surf, pygame.Rect(MARGIN, h - BAR_H - LEGEND_H - 6, w - MARGIN * 2, BAR_H), t)
            self.draw_legend(surf, pygame.Rect(MARGIN, h - LEGEND_H, w - MARGIN * 2, LEGEND_H))
            self.draw_toasts(surf)
        if self.confirm_exit:
            self.draw_dialog(surf, "Exit the jukebox?", "SELECT yes     BACK no")

    def draw_header(self, surf, t) -> None:
        th, c = self.theme, self.theme.c
        w = self.size[0]
        title = "JukeTube"
        # Chasing marquee lights.
        for i in range(0, w, 28):
            on = (i // 28 + int(t * 6)) % 3 == 0
            pygame.draw.circle(surf, c["warn"] if on else (60, 50, 40), (i + 14, 6), 3)
        glow = th.text(title, 46, c["accent"], title=True)
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            g = glow.copy()
            g.set_alpha(int(60 + 50 * pulse(t, 0.6)))
            surf.blit(g, (MARGIN + dx, 14 + dy))
        surf.blit(th.text(title, 46, c["text"], title=True), (MARGIN, 14))
        subtitle_x = MARGIN + th.font(46, title=True).size(title)[0] + 14
        subtitle = th.font(20, script=True).render("  by Vince Baker", True, c["muted"])
        surf.blit(subtitle, (subtitle_x, 26))

        eng = self.engine
        if eng.free_play:
            label, col = "FREE PLAY", c["good"]
        elif eng.credits:
            label, col = f"CREDITS {eng.credits}", c["accent2"]
        else:
            label = "INSERT COIN"
            col = c["warn"] if pulse(t, 1.5) > 0.4 else c["muted"]
        img = th.text(label, 30, col, bold=True, title=True)
        surf.blit(img, img.get_rect(topright=(w - MARGIN, 20)))
        clock = th.text(time.strftime("%H:%M"), 24, c["muted"], bold=True)
        surf.blit(clock, clock.get_rect(topright=(w - MARGIN - img.get_width() - 30, 25)))
        pygame.draw.line(surf, c["accent2"], (MARGIN, HEADER_H), (w - MARGIN, HEADER_H), 1)

    def draw_controls(self, surf, rect) -> None:
        th, c = self.theme, self.theme.c
        canvas = pygame.Surface((360, 344))
        canvas.fill(c["panel"])
        pygame.draw.rect(canvas, c["panel_hi"], canvas.get_rect(), 2, border_radius=12)
        canvas.blit(th.text("CONTROLS", 22, c["accent2"], bold=True), (16, 10))
        draw_labelled_layout(canvas, pygame.Rect(8, 40, 344, 210), th, self.control_labels())
        canvas.blit(th.text("↑ ↓ Browse   ← → Page / keyboard", 15, c["muted"]), (16, 254))
        canvas.blit(th.text("Right group: upper pair VOL +", 14, c["accent2"]), (16, 274))
        canvas.blit(th.text("lower pair VOL − · single taps unchanged", 14, c["accent2"]), (16, 291))
        note = "More actions: Playback controls" if self.button_positions else "Button calibration needed"
        canvas.blit(th.text(note, 14, c["muted"]), (16, 308))
        seconds = f"{self.inputs.exit_hold:g}"
        canvas.blit(th.text(f"QUIT: hold Coin + Start {seconds}s", 14, c["warn"]), (16, 325))
        surf.blit(pygame.transform.smoothscale(canvas, rect.size), rect)

    def control_labels(self) -> dict[str, str]:
        names = {Action.SELECT: "Select", Action.BACK: "Back", Action.ALT: "Favourite",
                 Action.START: "Start", Action.COIN: "Coin", Action.PROFILE_NEXT: "Profile",
                 Action.SKIP: "Skip",
                 Action.PAUSE: "Pause", Action.VOLUME_DOWN: "Vol −",
                 Action.VOLUME_UP: "Vol +", Action.EXIT: "Exit"}
        labels = {position: names.get(self.inputs.button_action(record.get("instance_id", -1),
                                                                  record["button"]), "Unused")
                  for position, record in self.button_positions.items()}
        # Mark physical positions participating in the two global shortcuts,
        # even before calibration has supplied the encoder button numbers.
        labels.update({"right_upper": "VOL +", "right_middle": "VOL +/-", "right_lower": "VOL −"})
        return labels

    def draw_breadcrumb(self, surf, content) -> None:
        th, c = self.theme, self.theme.c
        parts = [s.title for s in self.stack if s.title]
        crumb = "  ›  ".join(parts[-3:])
        img = th.text(crumb, 24, c["accent2"], bold=True, max_width=content.w)
        surf.blit(img, (content.x, HEADER_H + 10))

    def draw_up_next(self, surf, r, t) -> None:
        th, c = self.theme, self.theme.c
        pygame.draw.rect(surf, (*c["panel"],), r, border_radius=14)
        pygame.draw.rect(surf, c["panel_hi"], r, 2, border_radius=14)
        surf.blit(th.text("UP NEXT", 22, c["accent"], bold=True, title=True), (r.x + 16, r.y + 12))
        entries = self.engine.up_next
        count = sum(1 for e in entries if e.requested)
        if count:
            n = th.text(f"{count} request{'s' if count != 1 else ''}", 16, c["muted"])
            surf.blit(n, n.get_rect(topright=(r.right - 16, r.y + 18)))
        y = r.y + 50
        if not entries:
            msg = "Pick a song!" if self.engine.can_afford() else "Insert coin to pick a song"
            img = th.text(msg, 20, c["muted"], max_width=r.w - 32)
            surf.blit(img, (r.x + 16, y + 6))
            return
        row_h = 54
        for i, e in enumerate(entries):
            if y + row_h > r.bottom - 6:
                more = len(entries) - i
                img = th.text(f"+ {more} more", 16, c["muted"])
                surf.blit(img, (r.x + 16, y + 2))
                break
            art = self.art.get(e.track.art_url, 42) or placeholder_art(42, e.track.title, th)
            surf.blit(art, (r.x + 16, y))
            num = th.text(str(i + 1) if e.requested else "~", 14, c["accent2"] if e.requested else c["muted"], bold=True)
            surf.blit(num, (r.x + 66, y + 2))
            surf.blit(th.text(e.track.title, 19, c["text"], max_width=r.w - 106), (r.x + 88, y))
            surf.blit(th.text(e.track.artist, 15, c["muted"], max_width=r.w - 106), (r.x + 88, y + 24))
            y += row_h

    def _np_info(self):
        np = self.engine.now_playing
        if not np:
            return None, 0.0, None
        dur = self.player.duration or np.track.duration
        return np, self.player.position, dur

    def draw_now_playing(self, surf, r, t) -> None:
        th, c = self.theme, self.theme.c
        glow_rect(surf, r, c["accent2"], radius=16, spread=10, strength=50)
        pygame.draw.rect(surf, c["panel"], r, border_radius=16)
        pygame.draw.rect(surf, c["accent2"], r, 2, border_radius=16)
        np, pos, dur = self._np_info()
        art_size = r.h - 20
        if not np:
            surf.blit(placeholder_art(art_size, "idle", th), (r.x + 10, r.y + 10))
            msg = "Silence… pick a song!" if self.engine.can_afford() else "Insert coin and pick a song!"
            img = th.text(msg, 28, c["muted"], bold=True)
            surf.blit(img, (r.x + art_size + 30, r.centery - img.get_height() // 2))
            return
        art = self.art.get(np.track.art_url, art_size) or placeholder_art(art_size, np.track.title, th)
        surf.blit(art, (r.x + 10, r.y + 10))
        x = r.x + art_size + 30
        label = "NOW PLAYING" if np.requested else "AUTOPLAY"
        if getattr(self.player, "loading", False):
            label = "LOADING" + "." * (int(t * 3) % 4)
        elif self.player.paused:
            label = "PAUSED"
        surf.blit(th.text(label, 15, c["accent"], bold=True), (x, r.y + 12))
        eq_x = x + 140
        _equalizer(surf, eq_x, r.y + 12, t, c["accent2"], active=not self.player.paused and pos > 0)
        right_w = 220
        surf.blit(th.text(np.track.title, 30, c["text"], bold=True, max_width=r.right - x - right_w),
                  (x, r.y + 30))
        surf.blit(th.text(np.track.artist, 20, c["accent2"], max_width=r.right - x - right_w), (x, r.y + 66))
        # Progress bar.
        bar = pygame.Rect(r.right - right_w + 10, r.y + 56, right_w - 30, 8)
        pygame.draw.rect(surf, c["panel_hi"], bar, border_radius=4)
        if dur:
            frac = max(0.0, min(1.0, pos / dur))
            pygame.draw.rect(surf, c["accent"], (bar.x, bar.y, int(bar.w * frac), bar.h), border_radius=4)
            pygame.draw.circle(surf, c["text"], (bar.x + int(bar.w * frac), bar.centery), 7)
        times = th.text(f"{format_duration(pos)} / {format_duration(dur)}", 18, c["muted"])
        surf.blit(times, times.get_rect(midtop=(bar.centerx, bar.bottom + 10)))
        vol = th.text(f"VOL {self.player.volume}", 15, c["muted"], bold=True)
        surf.blit(vol, vol.get_rect(bottomright=(bar.right, bar.y - 12)))

    def draw_legend(self, surf, r) -> None:
        th, c = self.theme, self.theme.c
        screen_hint = self.stack[-1].hint
        parts = [screen_hint]
        for extra in ("BACK back", "START settings", "SKIP skip" if self.engine.cfg.allow_skip else "",
                      "COIN credit" if not self.engine.free_play else ""):
            if extra and extra.split()[0] not in screen_hint:
                parts.append(extra)
        text = "     ".join(p for p in parts if p)
        img = th.text(text, 17, c["muted"], max_width=r.w)
        surf.blit(img, img.get_rect(center=r.center))

    def draw_toasts(self, surf) -> None:
        th, c = self.theme, self.theme.c
        # Newest toast sits just above the now-playing bar; older ones stack up.
        y = self.size[1] - BAR_H - LEGEND_H - 20
        for msg, _ in reversed(self.toasts[-2:]):
            img = th.text(msg, 22, c["text"], bold=True, max_width=self.size[0] - 200)
            box = img.get_rect(midbottom=(self.size[0] // 2, y)).inflate(36, 18)
            glow_rect(surf, box, c["accent"], spread=8, strength=80)
            pygame.draw.rect(surf, c["panel_hi"], box, border_radius=12)
            pygame.draw.rect(surf, c["accent"], box, 2, border_radius=12)
            surf.blit(img, img.get_rect(center=box.center))
            y = box.top - 8

    def draw_dialog(self, surf, title, hint) -> None:
        th, c = self.theme, self.theme.c
        shade = pygame.Surface(self.size, pygame.SRCALPHA)
        shade.fill((0, 0, 0, 170))
        surf.blit(shade, (0, 0))
        box = pygame.Rect(0, 0, 560, 200)
        box.center = (self.size[0] // 2, self.size[1] // 2)
        glow_rect(surf, box, c["accent"], spread=14, strength=110)
        pygame.draw.rect(surf, c["panel"], box, border_radius=18)
        pygame.draw.rect(surf, c["accent"], box, 3, border_radius=18)
        t1 = th.text(title, 36, c["text"], bold=True, title=True)
        surf.blit(t1, t1.get_rect(center=(box.centerx, box.y + 70)))
        t2 = th.text(hint, 22, c["accent2"], bold=True)
        surf.blit(t2, t2.get_rect(center=(box.centerx, box.y + 140)))

    def draw_attract(self, surf, t) -> None:
        """Big now-playing view shown when the cabinet has been idle."""
        th, c = self.theme, self.theme.c
        w, h = self.size
        np, pos, dur = self._np_info()
        if np is None:
            self.draw_screensaver(surf, t)
            return
        size = int(h * 0.55)
        ax, ay = int(w * 0.08), (h - size) // 2 - 20
        bob = int(math.sin(t * 1.2) * 6)
        art = self.art.get(np.track.art_url, size) or placeholder_art(size, np.track.title, th)
        frame = pygame.Rect(ax, ay + bob, size, size)
        glow_rect(surf, frame, c["accent"], radius=18, spread=24, strength=int(80 + 60 * pulse(t, 0.8)))
        surf.blit(art, frame)
        x = frame.right + 60
        maxw = w - x - 60
        surf.blit(th.text("NOW PLAYING", 24, c["accent"], bold=True, title=True), (x, ay + 10))
        surf.blit(th.text(np.track.title, 54, c["text"], bold=True, max_width=maxw), (x, ay + 50))
        surf.blit(th.text(np.track.artist, 34, c["accent2"], max_width=maxw), (x, ay + 120))
        if np.track.album:
            surf.blit(th.text(np.track.album, 24, c["muted"], max_width=maxw), (x, ay + 166))
        bar = pygame.Rect(x, ay + 220, maxw, 10)
        pygame.draw.rect(surf, c["panel_hi"], bar, border_radius=5)
        if dur:
            pygame.draw.rect(surf, c["accent"], (bar.x, bar.y, int(bar.w * min(1, pos / dur)), bar.h),
                             border_radius=5)
        surf.blit(th.text(f"{format_duration(pos)} / {format_duration(dur)}", 22, c["muted"]),
                  (x, bar.bottom + 12))
        _equalizer(surf, x, ay + 290, t, c["accent2"], bars=24, width=maxw, height=80,
                   active=not self.player.paused)
        nxt = self.engine.up_next[:3]
        if nxt:
            surf.blit(th.text("UP NEXT", 20, c["accent"], bold=True), (x, ay + 400))
            for i, e in enumerate(nxt):
                surf.blit(th.text(f"{e.track.title} — {e.track.artist}", 20, c["text"], max_width=maxw),
                          (x, ay + 430 + i * 28))
        prompt = "INSERT COIN" if not self.engine.can_afford() else "PRESS ANY BUTTON TO PICK A SONG"
        if pulse(t, 1.0) > 0.3:
            img = th.text(prompt, 30, c["warn"], bold=True, title=True)
            surf.blit(img, img.get_rect(midbottom=(w // 2, h - 30)))

    def draw_screensaver(self, surf, t) -> None:
        """Float a full-screen collage of covers from played genres and songs."""
        th, c = self.theme, self.theme.c
        w, h = surf.get_size()
        items = self._screensaver_items()
        if not items:
            logo = th.text("JukeTube", 64, c["text"], title=True)
            surf.blit(logo, logo.get_rect(center=(w // 2, h // 2)))
            return

        started = self.screensaver_started or time.monotonic()
        elapsed = max(0.0, time.monotonic() - started)
        count = min(10, max(6, len(items)))
        base_size = min(h, w) * 0.31
        sizes = [0.95, 0.74, 0.62, 0.86, 0.68, 0.54, 0.78, 0.60, 0.70, 0.50]
        for i in range(count):
            item = items[i % len(items)]
            size = max(96, int(base_size * sizes[i % len(sizes)]))
            angle = elapsed * 0.20 + i * math.tau / count
            # Each cover follows its own drifting oval, sweeping across all
            # four edges instead of sitting in a fixed panel.
            x = w * 0.5 + math.cos(angle) * w * 0.43
            y = h * 0.5 + math.sin(angle * 1.23 + i * 0.24) * h * 0.43
            rect = pygame.Rect(0, 0, size, size)
            rect.center = (int(x), int(y))
            cover = self.art.get(item.get("art_url"), size)
            if cover is None:
                cover = self._screensaver_placeholder(size, item.get("title", "JukeTube"))
            # A slow pulse adds a soft dissolve while the whole collage moves.
            alpha = int(205 + 50 * pulse(elapsed + i * 0.71, 0.18))
            shadow = rect.inflate(12, 12)
            pygame.draw.rect(surf, (8, 5, 20), shadow, border_radius=max(8, size // 24))
            pygame.draw.rect(surf, c["accent"], shadow, 2, border_radius=max(8, size // 24))
            image = cover.copy()
            image.set_alpha(alpha)
            surf.blit(image, rect)

    def _screensaver_placeholder(self, size: int, title: str) -> pygame.Surface:
        key = (title, size)
        cache = getattr(self, "_screensaver_placeholder_cache", None)
        if cache is None:
            cache = self._screensaver_placeholder_cache = {}
        if key not in cache:
            cache[key] = placeholder_art(size, title, self.theme)
        return cache[key]


def _equalizer(surf, x, y, t, color, bars=8, width=70, height=14, active=True) -> None:
    gap = 3
    bw = max(2, (width - gap * (bars - 1)) // bars)
    for i in range(bars):
        if active:
            v = 0.5 + 0.5 * math.sin(t * (3.1 + i * 0.37) + i * 1.7) * math.sin(t * 1.3 + i)
            v = 0.15 + 0.85 * abs(v)
        else:
            v = 0.12
        bh = max(2, int(height * v))
        pygame.draw.rect(surf, color, (x + i * (bw + gap), y + height - bh, bw, bh), border_radius=2)


__all__ = ["App", "ListScreen"]
