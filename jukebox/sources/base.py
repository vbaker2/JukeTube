"""The Source interface. Every music provider (YouTube Music today, others
later) implements this so the engine and UI never talk to a provider directly.

All methods may block on the network; the UI calls them from worker threads.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Collection, Item, Shelf, Track


class SourceError(Exception):
    pass


class Source(ABC):
    name: str = "source"
    display_name: str = "Source"

    @abstractmethod
    def home(self) -> list[Shelf]:
        """Top-level browse sections (charts, genres, featured...)."""

    @abstractmethod
    def search(self, query: str) -> list[Item]:
        """Search the catalogue. Returns tracks and collections."""

    @abstractmethod
    def expand(self, collection: Collection) -> list[Item]:
        """Open a collection (playlist, album, artist, genre)."""

    @abstractmethod
    def radio(self, track: Track, limit: int = 25) -> list[Track]:
        """Tracks similar to `track`, used for idle autoplay."""

    @abstractmethod
    def playable_url(self, track: Track) -> str:
        """A URL or path the audio player can open for this track."""

    def house_music(self) -> list[Track]:
        """Tracks to play when nobody has requested anything. By default the
        first playlist on the home screen; sources can override."""
        for shelf in self.home():
            for item in shelf.items:
                if isinstance(item, Collection) and item.kind == "playlist":
                    return [t for t in self.expand(item) if isinstance(t, Track)]
        return []
