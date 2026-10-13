"""YouTube Music source, built on the unofficial ytmusicapi client.

Catalogue/browse data comes from ytmusicapi; audio is played by mpv through
yt-dlp, so `playable_url` just returns a music.youtube.com watch URL.

ytmusicapi scrapes YouTube's internal API and individual endpoints break from
time to time, so every call here is wrapped so one broken endpoint degrades a
single menu instead of taking the jukebox down.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..models import Collection, Item, Shelf, Track
from .base import Source, SourceError

log = logging.getLogger(__name__)

DEFAULT_GENRES = [
    "Pop", "Rock", "Hip-Hop", "R&B", "Dance & Electronic", "Country",
    "Indie & Alternative", "Metal", "Classic Rock", "80s", "90s", "2000s",
    "Disco", "Funk", "Soul", "Reggae", "Latin", "Jazz", "Blues", "Punk",
    "Classical", "Soundtracks", "Video Game Music", "Chill",
]


def _best_thumb(thumbs: Any, size: int = 300) -> str | None:
    if not thumbs or not isinstance(thumbs, list):
        return None
    url = thumbs[-1].get("url") if isinstance(thumbs[-1], dict) else None
    if not url:
        return None
    # googleusercontent thumbnails encode their size in the URL; ask for a
    # sharper one than the 60px the search API usually hands back.
    url = re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url)
    url = re.sub(r"=s\d+$", f"=s{size}", url)
    return url


def _parse_length(text: str | None) -> int | None:
    if not text:
        return None
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def _artists(data: dict) -> str:
    artists = data.get("artists") or []
    names = [a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")]
    if names:
        return ", ".join(names)
    return data.get("artist") or data.get("author") or ""


class YouTubeMusicSource(Source):
    name = "ytmusic"
    display_name = "YouTube Music"

    def __init__(
        self,
        auth_file: str | None = None,
        language: str = "en",
        location: str = "",
        chart_country: str = "ZZ",
        allow_explicit: bool = True,
        genres: list[str] | None = None,
        client: Any = None,
    ):
        if client is None:
            from ytmusicapi import YTMusic

            client = YTMusic(auth_file or None, language=language, location=location)
        self.yt = client
        self.authenticated = bool(auth_file)
        self.chart_country = chart_country
        self.allow_explicit = allow_explicit
        self.genres = genres or DEFAULT_GENRES

    # ------------------------------------------------------------------ parse
    def _track(self, data: dict, album: str = "", art_url: str | None = None) -> Track | None:
        if not isinstance(data, dict):
            return None
        vid = data.get("videoId")
        if not vid or data.get("isAvailable") is False:
            return None
        explicit = bool(data.get("isExplicit"))
        if explicit and not self.allow_explicit:
            return None
        duration = data.get("duration_seconds") or _parse_length(
            data.get("duration") or data.get("length")
        )
        album_info = data.get("album")
        if isinstance(album_info, dict):
            album = album_info.get("name") or album
        thumbs = data.get("thumbnails") or data.get("thumbnail")
        return Track(
            id=vid,
            title=data.get("title") or "Unknown",
            artist=_artists(data),
            album=album,
            duration=duration,
            art_url=_best_thumb(thumbs) or art_url,
            source=self.name,
            explicit=explicit,
        )

    def _tracks(self, rows: Any, **kw) -> list[Track]:
        out = []
        for row in rows or []:
            t = self._track(row, **kw)
            if t:
                out.append(t)
        return out

    def _collection(self, data: dict) -> Collection | None:
        """Turn a playlist/album/artist search or shelf result into a Collection."""
        if not isinstance(data, dict):
            return None
        thumb = _best_thumb(data.get("thumbnails"))
        rtype = data.get("resultType")
        browse = data.get("browseId") or ""
        pid = data.get("playlistId")
        title = data.get("title") or data.get("artist") or "Untitled"
        if rtype == "artist" or (browse.startswith("UC") and not pid):
            return Collection("artist", data.get("artist") or title, browse,
                              subtitle="Artist", art_url=thumb, source=self.name)
        if rtype == "album" or browse.startswith("MPRE"):
            sub = " · ".join(x for x in (data.get("type") or "Album", _artists(data),
                                         str(data.get("year") or "")) if x)
            return Collection("album", title, browse, subtitle=sub, art_url=thumb, source=self.name)
        if pid or browse.startswith(("VL", "PL", "RD", "OLAK")):
            ref = pid or browse
            if ref.startswith("VL"):
                ref = ref[2:]
            count = data.get("itemCount") or data.get("count")
            sub = " · ".join(x for x in ("Playlist", data.get("author") or "",
                                         f"{count} songs" if count else "") if x)
            return Collection("playlist", title, ref, subtitle=sub, art_url=thumb, source=self.name)
        return None

    def _item(self, data: dict) -> Item | None:
        if isinstance(data, dict) and data.get("videoId") and not data.get("playlistId"):
            return self._track(data)
        return self._collection(data)

    # -------------------------------------------------------------- browsing
    def _safe(self, what: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # ytmusicapi raises many types when YT changes
            log.warning("YouTube Music %s failed: %s", what, exc)
            return None

    def home(self) -> list[Shelf]:
        menu = [
            Collection("menu", "Charts", "charts", subtitle="What's hot right now", source=self.name),
            Collection("menu", "Genres", "genres", subtitle="Browse by style", source=self.name),
        ]
        if self.authenticated:
            menu.append(Collection("menu", "Your Library", "library",
                                   subtitle="Your saved playlists", source=self.name))
        shelves = [Shelf("Browse", menu)]
        for section in self._safe("home", self.yt.get_home, limit=6) or []:
            items = [i for i in (self._item(c) for c in section.get("contents") or []) if i]
            if items:
                shelves.append(Shelf(section.get("title") or "", items))
        return shelves

    def search(self, query: str) -> list[Item]:
        query = query.strip()
        if not query:
            return []
        results: list[Item] = []
        plan = [("songs", 20), ("artists", 3), ("albums", 6), ("featured_playlists", 6)]
        failures = 0
        for flt, limit in plan:
            rows = self._safe(f"search[{flt}]", self.yt.search, query, filter=flt, limit=limit)
            if rows is None:
                failures += 1
                continue
            for row in rows[:limit]:
                item = self._track(row) if flt == "songs" else self._collection(row)
                if item:
                    results.append(item)
        if failures == len(plan):
            raise SourceError("YouTube Music search is unavailable")
        return results

    def expand(self, c: Collection) -> list[Item]:
        if c.kind == "menu":
            return self._expand_menu(c.ref)
        if c.kind == "genre":
            # A bare genre name tends to surface regional playlists first;
            # "<genre> essentials" reliably finds YouTube Music's curated ones.
            query = f"{c.ref} essentials"
            rows = self._safe("genre", self.yt.search, query, filter="featured_playlists", limit=20)
            if not rows:
                rows = self._safe("genre", self.yt.search, query, filter="playlists", limit=20)
            return [i for i in (self._collection(r) for r in rows or []) if i]
        if c.kind == "playlist":
            data = self._safe("playlist", self.yt.get_playlist, c.ref, limit=200)
            if data is None:
                raise SourceError(f"Couldn't open playlist {c.title}")
            return self._tracks(data.get("tracks"), art_url=c.art_url)
        if c.kind == "album":
            data = self._safe("album", self.yt.get_album, c.ref)
            if data is None:
                raise SourceError(f"Couldn't open album {c.title}")
            art = _best_thumb(data.get("thumbnails")) or c.art_url
            return self._tracks(data.get("tracks"), album=data.get("title", ""), art_url=art)
        if c.kind == "artist":
            data = self._safe("artist", self.yt.get_artist, c.ref)
            if data is None:
                raise SourceError(f"Couldn't open artist {c.title}")
            items: list[Item] = list(self._tracks((data.get("songs") or {}).get("results")))
            for key in ("albums", "singles"):
                for row in (data.get(key) or {}).get("results") or []:
                    row = dict(row, resultType="album")
                    col = self._collection(row)
                    if col:
                        items.append(col)
            return items
        raise SourceError(f"Don't know how to open {c.kind}")

    def _expand_menu(self, ref: str) -> list[Item]:
        if ref == "genres":
            return [Collection("genre", g, g, subtitle="Genre", source=self.name) for g in self.genres]
        if ref == "charts":
            data = self._safe("charts", self.yt.get_charts, self.chart_country) or {}
            items: list[Item] = []
            for row in data.get("videos") or []:
                col = self._collection(row)
                if col:
                    items.append(col)
            for row in (data.get("artists") or [])[:20]:
                col = self._collection(dict(row, resultType="artist"))
                if col:
                    items.append(col)
            if not items:
                raise SourceError("Charts are unavailable right now")
            return items
        if ref == "library":
            rows = self._safe("library", self.yt.get_library_playlists, limit=100) or []
            return [i for i in (self._collection(r) for r in rows) if i]
        raise SourceError(f"Unknown menu {ref}")

    def radio(self, track: Track, limit: int = 25) -> list[Track]:
        data = self._safe("radio", self.yt.get_watch_playlist, videoId=track.id, limit=limit)
        if not data:
            return []
        return [t for t in self._tracks(data.get("tracks")) if t.id != track.id]

    def playable_url(self, track: Track) -> str:
        return f"https://music.youtube.com/watch?v={track.id}"
