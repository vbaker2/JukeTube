"""The pygame application: window, main loop and the persistent chrome
(header, Up Next panel, now-playing bar, toasts, attract mode)."""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor

import pygame

from ..engine import EngineConfig, Jukebox
from ..inputs import Action, InputMapper
from ..models import Collection, Track, format_duration
from ..player import Player
from ..sources import Source
from .art import ArtCache
from .screens import HomeScreen, ListScreen, Screen, SearchScreen
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
        disp = cfg["display"]
        self.size = (int(disp["width"]), int(disp["height"]))
        self.fps = int(disp["fps"])
        self.attract_after = float(disp["attract_after"])
        self.volume_step = int(cfg["audio"]["volume_step"])
        self.source = source
        self.player = player
        self._pool = ThreadPoolExecutor(4, thread_name_prefix="net")
        self.engine = Jukebox(source, player, EngineConfig(**cfg["jukebox"]), submit=self.submit)
        self.theme = Theme(disp.get("theme", "neon"))
        self.art = ArtCache(cache_dir)
        self.inputs = InputMapper(cfg["controls"])
        self.stack: list[Screen] = []
        self.toasts: list[tuple[str, float]] = []
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

    def queue_collection(self, c: Collection) -> None:
        self.toast(f"Fetching {c.title}…")
        fut = self.submit(self.source.expand, c)

        def done(f: Future) -> None:
            self._pending_queue.append(f)

        fut.add_done_callback(done)

    # --------------------------------------------------------------- loop
    def run(self) -> None:
        disp = self.cfg["display"]
        flags = pygame.SCALED
        if disp["fullscreen"]:
            flags |= pygame.FULLSCREEN
        pygame.display.set_caption(disp.get("title", "Jukebox"))
        self.screen = pygame.display.set_mode(self.size, flags, vsync=1 if self.fps >= 60 else 0)
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
        self.player.shutdown()
        self.art.shutdown()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def dispatch(self, action: Action) -> None:
        self.last_input = time.monotonic()
        eng = self.engine
        if self.attract:
            self.attract = False
            if action != Action.COIN:
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
        if self.stack[-1].handle(action):
            return
        if action == Action.BACK:
            self.pop()
        elif action == Action.START:
            self.open_search()
        elif action == Action.SKIP:
            if not eng.skip():
                self.toast("Skipping is disabled" if eng.now_playing else "Nothing playing")
        elif action == Action.PAUSE:
            eng.toggle_pause()
        elif action == Action.VOLUME_UP:
            self.toast(f"Volume {eng.change_volume(self.volume_step)}", 1.2)
        elif action == Action.VOLUME_DOWN:
            self.toast(f"Volume {eng.change_volume(-self.volume_step)}", 1.2)

    def update(self) -> None:
        self.engine.tick()
        while self.engine.messages:
            self.toast(self.engine.messages.popleft())
        while self._pending_queue:
            fut = self._pending_queue.pop(0)
            try:
                tracks = [i for i in fut.result() if isinstance(i, Track)]
                self.engine.request_many(tracks)
            except Exception as exc:
                self.toast(f"Couldn't queue: {exc}")
        self.stack[-1].update()
        now = time.monotonic()
        self.toasts = [(m, t) for m, t in self.toasts if t > now]
        if (not self.attract and self.attract_after > 0 and self.engine.now_playing
                and now - self.last_input > self.attract_after):
            self.attract = True

    # --------------------------------------------------------------- draw
    def draw(self, t: float) -> None:
        surf = self.screen
        surf.blit(self.theme.background(self.size), (0, 0))
        if self.attract:
            self.draw_attract(surf, t)
        else:
            w, h = self.size
            self.draw_header(surf, t)
            content = pygame.Rect(MARGIN, HEADER_H + 46, w - PANEL_W - MARGIN * 3,
                                  h - HEADER_H - 46 - BAR_H - LEGEND_H - 18)
            self.draw_breadcrumb(surf, content)
            self.stack[-1].draw(surf, content, t)
            panel = pygame.Rect(content.right + MARGIN, HEADER_H + 12, PANEL_W, content.bottom - HEADER_H - 12)
            self.draw_up_next(surf, panel, t)
            self.draw_now_playing(surf, pygame.Rect(MARGIN, h - BAR_H - LEGEND_H - 6, w - MARGIN * 2, BAR_H), t)
            self.draw_legend(surf, pygame.Rect(MARGIN, h - LEGEND_H, w - MARGIN * 2, LEGEND_H))
            self.draw_toasts(surf)
        if self.confirm_exit:
            self.draw_dialog(surf, "Exit the jukebox?", "SELECT yes     BACK no")

    def draw_header(self, surf, t) -> None:
        th, c = self.theme, self.theme.c
        w = self.size[0]
        title = self.cfg["display"].get("title", "JUKEBOX")
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
        for extra in ("BACK back", "START search", "SKIP skip" if self.engine.cfg.allow_skip else "",
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
        size = int(h * 0.55)
        ax, ay = int(w * 0.08), (h - size) // 2 - 20
        bob = int(math.sin(t * 1.2) * 6)
        art = (self.art.get(np.track.art_url, size) if np else None) or placeholder_art(
            size, np.track.title if np else "idle", th)
        frame = pygame.Rect(ax, ay + bob, size, size)
        glow_rect(surf, frame, c["accent"], radius=18, spread=24, strength=int(80 + 60 * pulse(t, 0.8)))
        surf.blit(art, frame)
        x = frame.right + 60
        maxw = w - x - 60
        if np:
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
