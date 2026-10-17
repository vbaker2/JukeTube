"""Source-agnostic data model shared by sources, the engine and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Track:
    """A single playable song."""

    id: str  # source-specific id (YouTube video id for YouTube Music)
    title: str
    artist: str = ""
    album: str = ""
    duration: int | None = None  # seconds
    art_url: str | None = None
    source: str = ""  # name of the Source that produced it
    explicit: bool = False
    genre: str = ""  # browsing genre, when the song came from a genre playlist

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"


@dataclass(frozen=True)
class Collection:
    """Something that can be opened to reveal more items.

    kind is one of: "playlist", "album", "artist", "genre", "menu".
    ref is an opaque source-specific reference used to expand it.
    """

    kind: str
    title: str
    ref: str
    subtitle: str = ""
    art_url: str | None = None
    source: str = ""
    genre: str = ""  # genre context carried by playlists from a genre menu


Item = Track | Collection


@dataclass
class Shelf:
    """A titled row of items, e.g. a section on the home page."""

    title: str
    items: list[Item] = field(default_factory=list)


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
