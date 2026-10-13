from __future__ import annotations

from .base import Source, SourceError


def create_source(name: str, options: dict | None = None) -> Source:
    """Instantiate a source by config name."""
    options = options or {}
    if name == "ytmusic":
        from .ytmusic import YouTubeMusicSource

        return YouTubeMusicSource(**options)
    raise SourceError(f"Unknown source: {name!r}")


__all__ = ["Source", "SourceError", "create_source"]
