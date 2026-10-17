"""Local favourites, saved atomically alongside the user's configuration."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from .models import Track


class Favourites:
    def __init__(self, path: Path):
        self.path = path
        self._tracks: dict[str, Track] = {}
        self.error: str | None = None
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("Expected a list of songs")
            tracks = []
            for record in records:
                track = Track(**record)
                if not all(isinstance(value, str) for value in
                           (track.id, track.title, track.artist, track.album, track.source)):
                    raise ValueError("Invalid song details")
                if track.duration is not None and not isinstance(track.duration, (int, float)):
                    raise ValueError("Invalid duration")
                if track.art_url is not None and not isinstance(track.art_url, str):
                    raise ValueError("Invalid artwork URL")
                tracks.append(track)
            self._tracks = {track.key: track for track in tracks}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            # Preserve an unreadable file rather than overwriting existing favourites.
            self.error = str(exc)

    def contains(self, track: Track) -> bool:
        return track.key in self._tracks

    def tracks(self, source: str) -> list[Track]:
        return [track for track in self._tracks.values() if track.source == source]

    def toggle(self, track: Track) -> bool:
        """Return whether the song is now saved. Failed writes leave memory intact."""
        if self.error:
            raise OSError(f"Couldn't read favourites: {self.error}")
        updated = dict(self._tracks)
        added = track.key not in updated
        if added:
            updated[track.key] = track
        else:
            del updated[track.key]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self.path.parent, delete=False) as file:
                temporary = Path(file.name)
                json.dump([asdict(t) for t in updated.values()], file, ensure_ascii=False, indent=2)
                file.write("\n")
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._tracks = updated
        return added
