"""Colours, fonts and small drawing helpers."""

from __future__ import annotations

import math
import zlib
from collections import OrderedDict
from pathlib import Path

import pygame

THEMES = {
    "neon": {
        "bg_top": (14, 8, 32),
        "bg_bottom": (4, 2, 12),
        "panel": (26, 18, 52),
        "panel_hi": (48, 30, 92),
        "text": (240, 236, 255),
        "muted": (150, 140, 190),
        "accent": (255, 60, 172),  # hot pink
        "accent2": (40, 220, 255),  # cyan
        "warn": (255, 200, 40),
        "good": (90, 240, 140),
    },
    "classic": {  # warm wood-and-chrome Wurlitzer vibe
        "bg_top": (40, 14, 8),
        "bg_bottom": (12, 4, 2),
        "panel": (62, 28, 16),
        "panel_hi": (110, 50, 22),
        "text": (255, 244, 220),
        "muted": (200, 160, 120),
        "accent": (255, 150, 30),
        "accent2": (255, 220, 120),
        "warn": (255, 90, 60),
        "good": (150, 230, 120),
    },
}

# Bundled (SIL OFL) so the cabinet looks the same whatever OS it runs.
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONTS = {
    "regular": FONT_DIR / "Barlow-Medium.ttf",
    "bold": FONT_DIR / "Barlow-Bold.ttf",
    "title": FONT_DIR / "Orbitron.ttf",
}


class Theme:
    def __init__(self, name: str = "neon"):
        self.c = THEMES.get(name, THEMES["neon"])
        self._fonts: dict[tuple, pygame.font.Font] = {}
        self._text_cache: OrderedDict[tuple, pygame.Surface] = OrderedDict()
        self._bg: pygame.Surface | None = None

    def font(self, size: int, bold: bool = False, title: bool = False) -> pygame.font.Font:
        key = (size, bold, title)
        if key not in self._fonts:
            path = FONTS["title" if title else "bold" if bold else "regular"]
            try:
                f = pygame.font.Font(path, size)
                if title:
                    f.bold = bold or True  # Orbitron's default weight is thin
            except (OSError, pygame.error):
                f = pygame.font.Font(None, int(size * 1.3))
            self._fonts[key] = f
        return self._fonts[key]

    def text(self, s: str, size: int, color, bold: bool = False, title: bool = False,
             max_width: int | None = None) -> pygame.Surface:
        key = (s, size, tuple(color), bold, title, max_width)
        surf = self._text_cache.get(key)
        if surf is not None:
            self._text_cache.move_to_end(key)
            return surf
        font = self.font(size, bold, title)
        if max_width and font.size(s)[0] > max_width:
            # Binary-search the longest prefix that fits with an ellipsis.
            lo, hi = 0, len(s)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.size(s[:mid].rstrip() + "…")[0] <= max_width:
                    lo = mid
                else:
                    hi = mid - 1
            s = s[:lo].rstrip() + "…"
        surf = font.render(s, True, color)
        self._text_cache[key] = surf
        if len(self._text_cache) > 600:
            self._text_cache.popitem(last=False)
        return surf

    def background(self, size: tuple[int, int]) -> pygame.Surface:
        if self._bg is None or self._bg.get_size() != size:
            w, h = size
            bg = pygame.Surface(size)
            top, bot = self.c["bg_top"], self.c["bg_bottom"]
            for y in range(h):
                t = y / max(1, h - 1)
                pygame.draw.line(bg, [round(a + (b - a) * t) for a, b in zip(top, bot)], (0, y), (w, y))
            # Faint retro perspective grid along the bottom.
            grid = pygame.Surface(size, pygame.SRCALPHA)
            gc = (*self.c["accent2"], 18)
            horizon = int(h * 0.55)
            for i in range(1, 14):
                y = horizon + int((h - horizon) * (i / 13) ** 2)
                pygame.draw.line(grid, gc, (0, y), (w, y))
            for i in range(-16, 17):
                pygame.draw.line(grid, gc, (w // 2 + i * 30, horizon), (w // 2 + i * 160, h))
            bg.blit(grid, (0, 0))
            self._bg = bg
        return self._bg


def glow_rect(surface: pygame.Surface, rect: pygame.Rect, color, radius: int = 12, spread: int = 8,
              strength: int = 90) -> None:
    """A soft neon halo around a rectangle."""
    glow = pygame.Surface((rect.w + spread * 2, rect.h + spread * 2), pygame.SRCALPHA)
    # draw.rect replaces alpha rather than blending, so paint outer (faint)
    # rings first and let each smaller, brighter ring overwrite the middle.
    for k in range(0, spread, 2):
        alpha = min(255, int(strength * ((k + 2) / spread) ** 2))
        pygame.draw.rect(glow, (*color[:3], alpha), glow.get_rect().inflate(-k * 2, -k * 2),
                         border_radius=radius + spread - k)
    surface.blit(glow, (rect.x - spread, rect.y - spread))


def pulse(t: float, speed: float = 2.0) -> float:
    """0..1 oscillation for blinking/breathing effects."""
    return 0.5 + 0.5 * math.sin(t * speed * math.pi)


def placeholder_art(size: int, seed: str, theme: Theme) -> pygame.Surface:
    """A deterministic vinyl-record tile used until real artwork arrives."""
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    h = zlib.crc32(seed.encode())
    base = theme.c["accent"] if h % 2 else theme.c["accent2"]
    pygame.draw.rect(surf, theme.c["panel_hi"], surf.get_rect(), border_radius=max(4, size // 10))
    c = size // 2
    pygame.draw.circle(surf, (10, 8, 16), (c, c), int(size * 0.42))
    for r in range(int(size * 0.18), int(size * 0.42), max(2, size // 24)):
        pygame.draw.circle(surf, (40, 36, 52), (c, c), r, 1)
    pygame.draw.circle(surf, base, (c, c), int(size * 0.15))
    pygame.draw.circle(surf, (10, 8, 16), (c, c), max(2, size // 40))
    return surf
