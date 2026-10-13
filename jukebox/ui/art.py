"""Album art: fetched off-thread, cached on disk, decoded on the main thread."""

from __future__ import annotations

import hashlib
import io
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pygame
import requests

log = logging.getLogger(__name__)


class ArtCache:
    def __init__(self, cache_dir: Path, workers: int = 4):
        self.dir = cache_dir / "art"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="art")
        self._pending: dict[str, Future] = {}
        self._raw: dict[str, pygame.Surface | None] = {}
        self._scaled: dict[tuple[str, int], pygame.Surface] = {}
        self._session = requests.Session()

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode()).hexdigest() + ".img")

    def _fetch(self, url: str) -> bytes | None:
        path = self._path(url)
        if path.exists():
            return path.read_bytes()
        try:
            r = self._session.get(url, timeout=10)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.debug("art fetch failed %s: %s", url, exc)
            return None
        path.write_bytes(r.content)
        return r.content

    def get(self, url: str | None, size: int) -> pygame.Surface | None:
        """Scaled art if it's ready; otherwise starts loading and returns None."""
        if not url:
            return None
        scaled = self._scaled.get((url, size))
        if scaled is not None:
            return scaled
        if url in self._raw:
            raw = self._raw[url]
            if raw is None:
                return None
            scaled = pygame.transform.smoothscale(raw, (size, size))
            self._scaled[(url, size)] = scaled
            return scaled
        fut = self._pending.get(url)
        if fut is None:
            self._pending[url] = self._pool.submit(self._fetch, url)
            return None
        if not fut.done():
            return None
        del self._pending[url]
        data = fut.result()
        raw = None
        if data:
            try:
                raw = _square(pygame.image.load(io.BytesIO(data)).convert())
            except (pygame.error, ValueError) as exc:
                log.debug("art decode failed %s: %s", url, exc)
        self._raw[url] = raw
        if len(self._scaled) > 400:
            self._scaled.clear()
        return self.get(url, size)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _square(img: pygame.Surface) -> pygame.Surface:
    """Centre-crop to a square (YouTube video thumbnails are 16:9)."""
    w, h = img.get_size()
    if w == h:
        return img
    s = min(w, h)
    return img.subsurface(pygame.Rect((w - s) // 2, (h - s) // 2, s, s)).copy()
