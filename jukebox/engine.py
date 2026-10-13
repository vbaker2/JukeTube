"""The jukebox brain: request queue, credits and idle autoplay.

The engine is UI-agnostic and single-threaded: the UI calls `tick()` every
frame. Network work (fetching autoplay radio) is handed to a `submit`
callable that runs it off-thread and returns a Future.
"""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable

from .models import Collection, Track
from .player import Player
from .sources import Source

log = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    free_play: bool = True
    songs_per_credit: int = 1
    max_credits: int = 99
    max_queue: int = 50
    max_per_track_in_queue: int = 1  # stops the same song being queued twice
    allow_skip: bool = True
    autoplay: bool = True  # play radio/house music when nobody has paid
    autoplay_delay: float = 3.0  # seconds of silence before autoplay kicks in
    autoplay_seed: str = ""  # optional playlist id for house music
    autoplay_radio: bool = False  # True: follow on with songs like the last one played
    interrupt_autoplay: bool = True  # a paid request cuts off house music
    history_size: int = 200


@dataclass
class QueueEntry:
    track: Track
    requested: bool  # False for autoplay filler


class Jukebox:
    def __init__(self, source: Source, player: Player, config: EngineConfig | None = None,
                 submit: Callable[..., Future] | None = None, clock=time.monotonic):
        self.source = source
        self.player = player
        self.cfg = config or EngineConfig()
        self._submit = submit
        self._clock = clock
        self.queue: deque[QueueEntry] = deque()
        self.autoplay_pool: deque[Track] = deque()
        self.now_playing: QueueEntry | None = None
        self.history: deque[Track] = deque(maxlen=self.cfg.history_size)
        self.credits = 0  # in songs, not coins
        self._silent_since: float | None = clock()
        self._radio_future: Future | None = None
        self._failures = 0
        self.messages: deque[str] = deque(maxlen=10)

    # ------------------------------------------------------------- credits
    @property
    def free_play(self) -> bool:
        return self.cfg.free_play

    def insert_coin(self) -> None:
        self.credits = min(self.cfg.max_credits, self.credits + self.cfg.songs_per_credit)
        self._notify(f"Credit added - {self.credits} song{'s' if self.credits != 1 else ''}")

    def can_afford(self, n: int = 1) -> bool:
        return self.free_play or self.credits >= n

    # --------------------------------------------------------------- queue
    def _notify(self, msg: str) -> None:
        log.info(msg)
        self.messages.append(msg)

    def count_in_queue(self, track: Track) -> int:
        n = sum(1 for e in self.queue if e.requested and e.track.key == track.key)
        if self.now_playing and self.now_playing.requested and self.now_playing.track.key == track.key:
            n += 1
        return n

    @property
    def requested_count(self) -> int:
        return sum(1 for e in self.queue if e.requested)

    def request(self, track: Track) -> tuple[bool, str]:
        """A paying customer picks a song. Returns (accepted, message)."""
        if not self.can_afford():
            msg = "Insert coin to pick a song"
        elif self.requested_count >= self.cfg.max_queue:
            msg = "Queue is full"
        elif self.count_in_queue(track) >= self.cfg.max_per_track_in_queue:
            msg = f"{track.title} is already queued"
        else:
            if not self.free_play:
                self.credits -= 1
            # Requests go after other requests but ahead of autoplay filler.
            idx = sum(1 for e in self.queue if e.requested)
            self.queue.insert(idx, QueueEntry(track, requested=True))
            ahead = idx + (1 if self.now_playing else 0)
            msg = f"Queued: {track.title}" + (f" ({ahead} ahead)" if ahead else "")
            self._drop_filler()
            self._notify(msg)
            return True, msg
        self._notify(msg)
        return False, msg

    def request_many(self, tracks: list[Track]) -> tuple[int, str]:
        added = 0
        for t in tracks:
            if not self.can_afford() or self.requested_count >= self.cfg.max_queue:
                break
            if self.count_in_queue(t) >= self.cfg.max_per_track_in_queue:
                continue
            if not self.free_play:
                self.credits -= 1
            self.queue.insert(self.requested_count, QueueEntry(t, requested=True))
            added += 1
        self._drop_filler()
        msg = f"Queued {added} song{'s' if added != 1 else ''}" if added else (
            "Insert coin to pick a song" if not self.can_afford() else "Nothing new to queue")
        self._notify(msg)
        return added, msg

    def _drop_filler(self) -> None:
        # Paid requests pre-empt house music, queued or (optionally) playing.
        self.queue = deque(e for e in self.queue if e.requested)
        if self.cfg.interrupt_autoplay and self.now_playing and not self.now_playing.requested:
            self.player.stop()
            self._finish_current()
            self._start_next()

    def remove(self, index: int) -> QueueEntry | None:
        if 0 <= index < len(self.queue):
            entry = self.queue[index]
            del self.queue[index]
            return entry
        return None

    def skip(self, force: bool = False) -> bool:
        if not (self.cfg.allow_skip or force) or not self.now_playing:
            return False
        self._notify(f"Skipped {self.now_playing.track.title}")
        self.player.stop()
        self._finish_current()
        self._start_next()
        return True

    def toggle_pause(self) -> None:
        if self.now_playing:
            self.player.set_paused(not self.player.paused)

    def change_volume(self, delta: int) -> int:
        self.player.set_volume(self.player.volume + delta)
        return self.player.volume

    # ------------------------------------------------------------ playback
    def _finish_current(self) -> None:
        if self.now_playing:
            self.history.appendleft(self.now_playing.track)
        self.now_playing = None
        self._silent_since = self._clock()

    def _start_next(self) -> bool:
        if not self.queue:
            return False
        entry = self.queue.popleft()
        self.now_playing = entry
        self._silent_since = None
        try:
            self.player.play(self.source.playable_url(entry.track))
        except Exception as exc:
            log.exception("play failed")
            self._notify(f"Couldn't play {entry.track.title}: {exc}")
            self._finish_current()
            return False
        return True

    def tick(self) -> None:
        reason = self.player.take_finished()
        if reason and self.now_playing:
            if reason == "error":
                self._failures += 1
                self._notify(f"Couldn't play {self.now_playing.track.title}")
            else:
                self._failures = 0
            self._finish_current()
        if self.now_playing is None:
            if self.queue:
                # Back off if every track is failing (e.g. network down).
                if self._failures < 5 or self._silence() > min(60, 2 ** self._failures):
                    self._start_next()
            elif self.cfg.autoplay and self._silence() >= self.cfg.autoplay_delay:
                self._autoplay()
        self._poll_radio()

    def _silence(self) -> float:
        return 0.0 if self._silent_since is None else self._clock() - self._silent_since

    # ------------------------------------------------------------ autoplay
    def _autoplay(self) -> None:
        while self.autoplay_pool:
            t = self.autoplay_pool.popleft()
            if not any(h.key == t.key for h in list(self.history)[:30]):
                self.queue.append(QueueEntry(t, requested=False))
                self._start_next()
                return
        self._fetch_radio()

    def _fetch_radio(self) -> None:
        if self._radio_future is not None or self._submit is None:
            return
        seed = self.history[0] if self.history else None
        self._radio_future = self._submit(self._load_radio, seed)

    def _load_radio(self, seed: Track | None) -> list[Track]:
        if seed is not None and self.cfg.autoplay_radio:
            tracks = self.source.radio(seed)
            if tracks:
                return tracks
        if self.cfg.autoplay_seed:
            items = self.source.expand(Collection("playlist", "House", self.cfg.autoplay_seed))
            tracks = [i for i in items if isinstance(i, Track)]
        else:
            tracks = self.source.house_music()
        random.shuffle(tracks)
        return tracks

    def _poll_radio(self) -> None:
        fut = self._radio_future
        if fut is None or not fut.done():
            return
        self._radio_future = None
        try:
            tracks = fut.result()
        except Exception as exc:
            log.warning("autoplay fetch failed: %s", exc)
            tracks = []
            # Don't hammer the API; wait before the next attempt.
            self._silent_since = self._clock() + 10
        self.autoplay_pool.extend(tracks)

    @property
    def up_next(self) -> list[QueueEntry]:
        return list(self.queue)
