"""An offline source with made-up songs, for trying the UI without network
access and for tests. Pair it with the simulated player (`--demo`)."""

from __future__ import annotations

from ..models import Collection, Item, Shelf, Track
from .base import Source

_ARTISTS = ["The Pixel Pushers", "Neon Arcade", "Chiptune Kids", "Joystick Jamboree",
            "Insert Coin", "High Score Heroes", "8-Bit Orchestra", "Player Two"]
_WORDS = ["Midnight", "Turbo", "Laser", "Galaxy", "Cherry", "Highway", "Arcade", "Dream",
          "Pixel", "Racer", "Heart", "Neon", "Summer", "Rocket", "Thunder", "Velvet"]


def _track(i: int) -> Track:
    title = f"{_WORDS[i % len(_WORDS)]} {_WORDS[(i * 7 + 3) % len(_WORDS)]}"
    return Track(id=f"demo{i}", title=title, artist=_ARTISTS[i % len(_ARTISTS)],
                 album=f"Volume {i % 5 + 1}", duration=150 + (i * 37) % 120, source="demo")


class DemoSource(Source):
    name = "demo"
    display_name = "Demo Library"

    def home(self) -> list[Shelf]:
        return [
            Shelf("Browse", [Collection("menu", "Genres", "genres", "Browse by style", source=self.name)]),
            Shelf("Arcade Classics", [Collection("playlist", f"Mixtape #{n}", f"mix{n}",
                                                 "Playlist · 12 songs", source=self.name)
                                      for n in range(1, 6)]),
        ]

    def search(self, query: str) -> list[Item]:
        q = query.lower()
        hits = [t for t in (_track(i) for i in range(120)) if q in t.title.lower() or q in t.artist.lower()]
        return hits[:30]

    def expand(self, c: Collection) -> list[Item]:
        if c.ref == "genres":
            return [Collection("genre", g, g, "Genre", source=self.name)
                    for g in ("Synthwave", "Chiptune", "Funk", "Rock")]
        seed = sum(map(ord, c.ref))
        return [_track(seed + i) for i in range(12)]

    def radio(self, track: Track, limit: int = 25) -> list[Track]:
        seed = sum(map(ord, track.id))
        return [_track(seed + i) for i in range(1, limit + 1)]

    def playable_url(self, track: Track) -> str:
        return f"demo://{track.id}"
