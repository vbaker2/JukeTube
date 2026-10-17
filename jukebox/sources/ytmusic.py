"""YouTube Music source, built on the unofficial ytmusicapi client.

Catalogue/browse data comes from ytmusicapi; audio is played by mpv through
yt-dlp, so `playable_url` just returns a music.youtube.com watch URL.

ytmusicapi scrapes YouTube's internal API and individual endpoints break from
time to time, so every call here is wrapped so one broken endpoint degrades a
single menu instead of taking the jukebox down.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import replace
from typing import Any

from ..models import Collection, Item, Shelf, Track
from .base import Source, SourceError

log = logging.getLogger(__name__)

# British and European styles, roughly in order of era. Each genre is found by
# searching playlists for `query`; only playlists whose title contains one of
# `match` are kept, because YouTube's playlist search pads niche queries with
# unrelated (often Bollywood or US country) playlists.
DEFAULT_GENRES = [
    {"name": "60s British Invasion", "about": "The Beatles, The Kinks, The Who, The Rolling Stones",
     "query": "60s british invasion", "match": ["60s", "sixties", "british invasion", "merseybeat"]},
    {"name": "70s Glam Rock", "about": "Bowie, T. Rex, Slade, Sweet, Roxy Music",
     "query": "70s glam rock", "match": ["glam"]},
    {"name": "Punk & New Wave", "about": "Sex Pistols, The Clash, The Jam, Buzzcocks",
     "query": "british punk new wave", "match": ["punk", "new wave"]},
    {"name": "2 Tone & Ska", "about": "The Specials, Madness, The Beat, The Selecter",
     "query": "2 tone ska", "match": ["ska", "2 tone", "two tone", "2-tone"]},
    {"name": "New Romantics & Synth-pop", "about": "Duran Duran, Depeche Mode, Pet Shop Boys",
     "query": "new romantics synth pop", "match": ["new romantic", "synth", "new wave"]},
    {"name": "80s Hits", "about": "The big hits of the eighties",
     "query": "80s hits", "match": ["80s", "eighties"]},
    {"name": "Madchester", "about": "The Stone Roses, Happy Mondays, Inspiral Carpets",
     "query": "madchester", "match": ["madchester", "baggy"]},
    {"name": "Britpop", "about": "Oasis, Blur, Pulp, Suede, Supergrass",
     "query": "britpop", "match": ["britpop", "brit pop", "brit-pop"]},
    {"name": "90s Hits", "about": "The big hits of the nineties",
     "query": "90s hits", "match": ["90s", "nineties"]},
    {"name": "Rave & Acid House", "about": "The Prodigy, Altern-8, SL2, 808 State",
     "query": "90s rave anthems", "match": ["rave", "acid house", "hardcore"]},
    {"name": "Handbag House", "about": "UK 90s piano house and club anthems",
     "query": "handbag house 90s UK", "match": ["handbag house", "handbag"]},
    {"name": "Drum & Bass and Jungle", "about": "Goldie, Roni Size, LTJ Bukem",
     "query": "drum and bass jungle classics", "match": ["drum", "jungle", "dnb", "d&b"]},
    {"name": "UK Garage", "about": "Artful Dodger, Craig David, Shanks & Bigfoot",
     "query": "uk garage classics", "match": ["garage", "ukg"]},
    {"name": "Big Beat & Trip Hop", "about": "Fatboy Slim, The Chemical Brothers, Massive Attack",
     "query": "big beat trip hop", "match": ["big beat", "trip hop", "trip-hop"]},
    {"name": "Dubstep", "about": "UK bass music, from dubstep roots to club anthems",
     "query": "UK dubstep classics", "match": ["dubstep", "brostep"]},
    {"name": "Eurodance", "about": "2 Unlimited, Haddaway, Culture Beat, Snap!",
     "query": "eurodance 90s", "match": ["eurodance", "euro dance", "90s dance"]},
    {"name": "Trance", "about": "Tiësto, ATB, Paul van Dyk, Darude",
     "query": "trance classics", "match": ["trance"]},
    {"name": "Big Room House", "about": "European festival house: Swedish House Mafia, Hardwell, Martin Garrix",
     "query": "big room house anthems", "match": ["big room", "bigroom"]},
    {"name": "2000s Indie", "about": "Arctic Monkeys, The Libertines, Kaiser Chiefs",
     "query": "2000s british indie", "match": ["indie"]},
    {"name": "Boy Band", "about": "Take That, Boyzone, Westlife, Blue, 5ive, East 17",
     "query": "British Irish boy bands", "match": ["boy band", "boyband", "take that", "boyzone",
                                                      "westlife", "5ive", "east 17"]},
    {"name": "2000s Hits", "about": "The big hits of the noughties",
     "query": "2000s hits", "match": ["2000s", "00s", "noughties"]},
    {"name": "Eurovision", "about": "Douze points",
     "query": "eurovision", "match": ["eurovision"]},
    {"name": "British Heavy Metal", "about": "Iron Maiden, Judas Priest, Black Sabbath",
     "query": "nwobhm british heavy metal", "match": ["nwobhm", "heavy metal", "metal"]},
    {"name": "Eesti muusika", "about": "Ultima Thule, Terminaator, Smilers, Vaiko Eplik",
     "query": "eesti muusika", "match": ["eesti", "estonia"]},
    {"name": "Classical", "about": "The great European composers",
     "query": "classical essentials", "match": ["classical", "symphon", "orchestra", "opera"]},
]

DEFAULT_ARTISTS = ["Back Street Boys", "The Prodigy", "Knife Party", "Swedish House Mafia"]

# Playlists whose titles contain any of these are dropped from genre lists:
# keeps the browse menus British/European rather than US or other regions.
DEFAULT_GENRE_EXCLUDE = [
    "r&b", "rnb", "hip hop", "hip-hop", "hiphop", "rap", "trap", "country", "cantopop",
    "mandopop", "k-pop", "kpop", "j-pop", "punjabi", "bollywood", "hindi", "tamil", "telugu",
    "egyptian", "arabic", "latin", "reggaeton", "naija", "afro", "gospel", "lullab",
]

# Each entry becomes its own "<name> Charts" tab on the home screen.
# The first one also supplies the house music played when the queue is empty.
DEFAULT_CHARTS = [
    {"name": "UK", "country": "GB"},
    {"name": "Russian", "country": "RU"},
    {"name": "Estonian", "country": "EE"},
]

_COUNTRY_NAMES = {"GB": "the UK", "US": "the US", "RU": "Russia", "EE": "Estonia", "ZZ": "the world",
                  "DE": "Germany", "FR": "France", "FI": "Finland", "LV": "Latvia", "LT": "Lithuania",
                  "UA": "Ukraine", "IE": "Ireland", "ES": "Spain", "IT": "Italy", "SE": "Sweden"}

# Order in which a country's chart playlists are preferred as its "Top" list.
_CHART_PREFERENCE = ("Top 100 Music Videos", "Daily Top Music Videos", "Trending 20")


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
        user: str | None = None,
        language: str = "en",
        location: str = "GB",
        charts: list[dict] | None = None,
        allow_explicit: bool = True,
        genres: list[str | dict] | None = None,
        genre_exclude: list[str] | None = None,
        client: Any = None,
    ):
        if client is None:
            from ytmusicapi import YTMusic

            client = YTMusic(auth_file or None, user=user or None,
                             language=language, location=location)
        self.yt = client
        self.authenticated = bool(auth_file)
        self.charts = [c for c in (charts if charts is not None else DEFAULT_CHARTS) if c.get("country")]
        self.allow_explicit = allow_explicit
        # Config may give plain names ("Britpop") or full {name, query, match, about} tables.
        self.genre_exclude = [x.lower() for x in (DEFAULT_GENRE_EXCLUDE if genre_exclude is None
                                                  else genre_exclude)]
        self.genres = [{"name": g} if isinstance(g, str) else g for g in (genres or DEFAULT_GENRES)]

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
            Collection("chart", f"{c.get('name') or c['country']} Charts", c["country"].upper(),
                       subtitle=f"Top 100 right now in {_COUNTRY_NAMES.get(c['country'].upper(), c['country'])}",
                       source=self.name)
            for c in self.charts
        ]
        menu.append(Collection("menu", "Genres", "genres", subtitle="Browse by style", source=self.name))
        menu.append(Collection("menu", "Artists", "artists", subtitle="Your favourite artists", source=self.name))
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
        if c.kind == "chart":
            return self._expand_chart(c.ref)
        if c.kind == "genre":
            return self._expand_genre(c.ref)
        if c.kind == "playlist":
            data = self._safe("playlist", self.yt.get_playlist, c.ref, limit=200)
            if data is None:
                raise SourceError(f"Couldn't open playlist {c.title}")
            tracks = self._tracks(data.get("tracks"), art_url=c.art_url)
            return [replace(track, genre=c.genre) for track in tracks] if c.genre else tracks
        if c.kind == "album":
            data = self._safe("album", self.yt.get_album, c.ref)
            if data is None:
                raise SourceError(f"Couldn't open album {c.title}")
            art = _best_thumb(data.get("thumbnails")) or c.art_url
            return self._tracks(data.get("tracks"), album=data.get("title", ""), art_url=art)
        if c.kind == "artist":
            artist_ref = c.ref
            if artist_ref.startswith("search:"):
                rows = self._safe("artist search", self.yt.search, artist_ref[7:],
                                  filter="artists", limit=5) or []
                match = None
                for row in rows:
                    candidate = self._collection(row)
                    if candidate and candidate.kind == "artist":
                        match = candidate
                        break
                if not match:
                    raise SourceError(f"Couldn't find artist {c.title}")
                artist_ref = match.ref
            data = self._safe("artist", self.yt.get_artist, artist_ref)
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
            return [Collection("genre", g["name"], g["name"], subtitle=g.get("about") or "Genre",
                               source=self.name) for g in self.genres]
        if ref == "artists":
            return [Collection("artist", name, f"search:{name}", subtitle="Artist",
                               source=self.name) for name in DEFAULT_ARTISTS]
        if ref == "library":
            rows = self._safe("library", self.yt.get_library_playlists, limit=100) or []
            return [i for i in (self._collection(r) for r in rows) if i]
        raise SourceError(f"Unknown menu {ref}")

    def _genre(self, name: str) -> dict:
        for g in self.genres:
            if g["name"] == name:
                return g
        return {"name": name}

    def _expand_genre(self, name: str) -> list[Item]:
        g = self._genre(name)
        query = g.get("query") or name
        match = [m.lower() for m in g.get("match") or [name]]
        found: list[Collection] = []
        seen: set[str] = set()
        # YouTube Music's own curated playlists first, then listener-made ones,
        # which are far more literal for niche scenes like Madchester.
        for flt in ("featured_playlists", "community_playlists"):
            for row in self._safe(f"genre[{flt}]", self.yt.search, query, filter=flt, limit=20) or []:
                col = self._collection(row)
                title = col.title.lower() if col else ""
                if (col and col.ref not in seen and any(m in title for m in match)
                        and not any(re.search(rf"\b{re.escape(x)}", title) for x in self.genre_exclude)):
                    seen.add(col.ref)
                    found.append(replace(col, genre=name))
        if not found:
            raise SourceError(f"No {name} playlists found right now")
        return found

    def _expand_chart(self, country: str) -> list[Item]:
        """A country's chart: its Top 100 songs first, then the other chart
        playlists and the country's top artists."""
        data = self._safe("charts", self.yt.get_charts, country) or {}
        playlists = [c for c in (self._collection(r) for r in data.get("videos") or []) if c]
        if not playlists:
            raise SourceError("Charts are unavailable right now")

        def rank(c: Collection) -> int:
            for i, prefix in enumerate(_CHART_PREFERENCE):
                if c.title.startswith(prefix):
                    return i
            return len(_CHART_PREFERENCE)

        playlists.sort(key=rank)
        top, others = playlists[0], playlists[1:]
        items: list[Item] = list(self.expand(top))
        items.extend(others)
        for row in (data.get("artists") or [])[:20]:
            col = self._collection(dict(row, resultType="artist"))
            if col:
                items.append(col)
        return items

    def house_music(self) -> list[Track]:
        if self.charts:
            try:
                return [i for i in self._expand_chart(self.charts[0]["country"]) if isinstance(i, Track)]
            except SourceError:
                pass
        return super().house_music()

    def radio(self, track: Track, limit: int = 25) -> list[Track]:
        data = self._safe("radio", self.yt.get_watch_playlist, videoId=track.id, limit=limit)
        if not data:
            return []
        return [t for t in self._tracks(data.get("tracks")) if t.id != track.id]

    def popular_dance(self, limit: int = 60) -> list[Track]:
        """Build a shuffled pool of mainstream UK dance and club tracks."""
        tracks: list[Track] = []
        seen: set[str] = set()
        queries = ("Kiss Dance", "UK dance hits", "UK club anthems")
        for query in queries:
            rows = self._safe(f"popular dance[{query}]", self.yt.search,
                              query, filter="songs", limit=25)
            for track in self._tracks(rows):
                if track.id not in seen:
                    seen.add(track.id)
                    tracks.append(track)
        random.shuffle(tracks)
        return tracks[:max(1, limit)]

    def playable_url(self, track: Track) -> str:
        return f"https://music.youtube.com/watch?v={track.id}"
