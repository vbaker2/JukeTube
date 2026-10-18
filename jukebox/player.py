"""Audio playback.

MpvPlayer runs mpv as a headless subprocess and drives it over its JSON IPC
socket (a named pipe on Windows). mpv's bundled ytdl hook uses yt-dlp to turn
YouTube URLs into audio streams, so mpv and yt-dlp are the only runtime
playback dependencies.

Two IPC connections are used: one owned by a reader thread, which registers
property observers and then only reads events; one for fire-and-forget
commands. That avoids interleaving reads and writes on one handle, which
blocks on Windows pipes.

SimulatedPlayer plays nothing but advances a clock; it is used for tests,
demo mode and as a fallback when mpv isn't installed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


class Player:
    """Interface the engine uses. Implementations must be thread-safe."""

    position: float = 0.0
    duration: float | None = None
    paused: bool = False
    volume: int = 80
    error: str | None = None

    def play(self, url: str) -> None: ...
    def stop(self) -> None: ...
    def set_paused(self, paused: bool) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def shutdown(self) -> None: ...

    def is_busy(self) -> bool:
        """True while a track is loading or playing."""
        raise NotImplementedError

    def take_finished(self) -> str | None:
        """Return and clear the end reason of the last track, if it ended
        ("eof" or "error")."""
        raise NotImplementedError


def find_mpv(configured: str | None = None) -> str | None:
    if configured:
        return configured if Path(configured).exists() or shutil.which(configured) else None
    found = shutil.which("mpv")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/mpv", "/usr/local/bin/mpv", "/usr/bin/mpv",
                      r"C:\Program Files\mpv\mpv.exe"):
        if Path(candidate).exists():
            return candidate
    return None


def find_ytdlp(configured: str | None = None) -> str | None:
    if configured:
        return configured
    # Prefer the yt-dlp installed next to this Python (our venv) so it stays
    # in step with the pip-pinned version; YouTube breaks old versions often.
    exe = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    local = Path(sys.executable).parent / exe
    if local.exists():
        return str(local)
    return shutil.which("yt-dlp")


class _IpcConn:
    """One client connection to mpv's IPC server."""

    def __init__(self, path: str):
        if os.name == "nt":
            self._f = open(path, "r+b", buffering=0)  # noqa: SIM115
            self._sock = None
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(path)
            self._f = self._sock.makefile("rb")
        self._lock = threading.Lock()

    def send(self, command: list) -> None:
        data = (json.dumps({"command": command}) + "\n").encode()
        with self._lock:
            if self._sock is not None:
                self._sock.sendall(data)
            else:
                self._f.write(data)
                self._f.flush()

    def readline(self) -> bytes:
        return self._f.readline()

    def close(self) -> None:
        try:
            if self._sock is not None:
                self._sock.shutdown(socket.SHUT_RDWR)
                self._sock.close()
            self._f.close()
        except OSError:
            pass


class MpvPlayer(Player):
    def __init__(self, mpv_path: str, ytdlp_path: str | None = None, volume: int = 80,
                 audio_device: str | None = None, extra_args: list[str] | None = None):
        self.volume = volume
        self._state_lock = threading.Lock()
        self._busy = False
        self._finished: str | None = None
        self._loading_since: float | None = None
        # After loadfile, events for the *previous* file can still arrive;
        # ignore playback events until mpv announces the new file started.
        self._awaiting_start = False
        self._mpv_path, self._ytdlp_path = mpv_path, ytdlp_path
        self._audio_device, self._extra_args = audio_device, list(extra_args or [])
        self._generation = 0  # bumped per mpv process; stale readers ignore themselves
        self._spawn_lock = threading.Lock()
        self._spawn()

    def _spawn(self) -> None:
        """Start (or restart) the mpv process and its two IPC connections."""
        if os.name == "nt":
            self._ipc_path = rf"\\.\pipe\jukebox-mpv-{uuid.uuid4().hex[:8]}"
        else:
            self._ipc_path = os.path.join(tempfile.gettempdir(), f"jukebox-mpv-{uuid.uuid4().hex[:8]}.sock")
        args = [
            self._mpv_path, "--idle=yes", "--no-video", "--no-terminal", "--really-quiet",
            "--force-window=no", "--audio-display=no", "--cache=yes",
            f"--input-ipc-server={self._ipc_path}", f"--volume={self.volume}",
            "--ytdl-format=bestaudio/best", "--keep-open=no",
        ]
        if self._ytdlp_path:
            args.append(f"--script-opts=ytdl_hook-ytdl_path={self._ytdlp_path}")
        if self._audio_device:
            args.append(f"--audio-device={self._audio_device}")
        args += self._extra_args
        log.info("Starting mpv: %s", " ".join(args))
        self._proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        self._generation += 1
        self._events = self._connect()
        self._cmd = self._connect()
        threading.Thread(target=self._drain, args=(self._cmd,), daemon=True).start()
        for i, prop in enumerate(("time-pos", "duration", "pause", "idle-active")):
            self._events.send(["observe_property", i + 1, prop])
        threading.Thread(target=self._read_events, args=(self._events, self._generation),
                         daemon=True).start()

    def _command(self, command: list) -> None:
        """Send a command; if mpv has died, restart it once and retry, so a
        crashed player never takes the whole jukebox down with it."""
        try:
            self._cmd.send(command)
            return
        except OSError as exc:
            log.warning("mpv command %s failed (%s); restarting mpv", command[0], exc)
        with self._spawn_lock:
            try:
                self._close_process()
                self._spawn()
                self._cmd.send(command)
            except (OSError, RuntimeError) as exc:
                log.error("Couldn't restart mpv: %s", exc)
                with self._state_lock:
                    self.error = "Audio player unavailable"
                raise RuntimeError("Audio player unavailable") from exc

    def _connect(self) -> _IpcConn:
        deadline = time.monotonic() + 10
        while True:
            if self._proc.poll() is not None:
                raise RuntimeError(f"mpv exited during startup (code {self._proc.returncode})")
            try:
                return _IpcConn(self._ipc_path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                if time.monotonic() > deadline:
                    raise RuntimeError("Timed out connecting to mpv IPC") from None
                time.sleep(0.05)

    @staticmethod
    def _drain(conn: _IpcConn) -> None:
        # Command replies must be read or the pipe buffer eventually fills.
        while conn.readline():
            pass

    def _read_events(self, conn: _IpcConn, generation: int) -> None:
        while True:
            try:
                line = conn.readline()
            except (OSError, ValueError):
                line = b""
            if generation != self._generation:
                return  # mpv was restarted; this connection is obsolete
            if not line:
                log.warning("mpv IPC closed")
                with self._state_lock:
                    if self._busy:
                        self._finished = "error"
                    self._busy = False
                    self.error = "Audio player stopped"
                return
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self._handle(msg)

    def _handle(self, msg: dict) -> None:
        event = msg.get("event")
        with self._state_lock:
            if event == "start-file":
                self._awaiting_start = False
                return
            if self._awaiting_start and (event == "end-file" or msg.get("name") in ("time-pos", "duration")):
                return
            if event == "property-change":
                name, data = msg.get("name"), msg.get("data")
                if name == "time-pos" and data is not None:
                    self.position = float(data)
                    self._loading_since = None
                elif name == "duration":
                    self.duration = float(data) if data is not None else None
                elif name == "pause":
                    self.paused = bool(data)
            elif event == "end-file":
                reason = msg.get("reason")
                if reason in ("eof", "error") and self._busy:
                    self._finished = reason
                    self._busy = False
                    if reason == "error":
                        self.error = msg.get("file_error") or "Playback failed"
                    log.info("mpv end-file: %s %s", reason, msg.get("file_error", ""))

    def play(self, url: str) -> None:
        with self._state_lock:
            self._busy = True
            self._finished = None
            self.position = 0.0
            self.duration = None
            self.error = None
            self._loading_since = time.monotonic()
            self._awaiting_start = True
        self._command(["loadfile", url, "replace"])
        self._command(["set_property", "pause", False])

    def _quiet(self, command: list) -> None:
        # Controls (skip/pause/volume) must never crash the UI loop.
        try:
            self._command(command)
        except RuntimeError:
            pass

    def stop(self) -> None:
        with self._state_lock:
            self._busy = False
            self._finished = None
        self._quiet(["stop"])

    def set_paused(self, paused: bool) -> None:
        self._quiet(["set_property", "pause", paused])

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(100, volume))
        self._quiet(["set_property", "volume", self.volume])

    def seek(self, seconds: float) -> None:
        self._quiet(["seek", seconds, "relative"])

    @property
    def loading(self) -> bool:
        return self._loading_since is not None

    def is_busy(self) -> bool:
        with self._state_lock:
            return self._busy

    def take_finished(self) -> str | None:
        with self._state_lock:
            reason, self._finished = self._finished, None
            return reason

    def shutdown(self) -> None:
        self._generation += 1  # the closing connection isn't an error
        self._close_process()

    def _close_process(self) -> None:
        try:
            self._cmd.send(["quit"])
        except OSError:
            pass
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=2)
        self._events.close()
        self._cmd.close()
        if os.name != "nt":
            try:
                os.unlink(self._ipc_path)
            except OSError:
                pass


class SimulatedPlayer(Player):
    """Silent stand-in: pretends every track lasts `default_length` seconds."""

    def __init__(self, default_length: float = 180.0, speed: float = 1.0, clock=time.monotonic):
        self.default_length = default_length
        self.speed = speed
        self._clock = clock
        self._lock = threading.Lock()
        self._started: float | None = None
        self._offset = 0.0
        self._finished: str | None = None
        self.url: str | None = None
        self.loading = False

    def _pos(self) -> float:
        if self._started is None:
            return self._offset
        return self._offset + (self._clock() - self._started) * self.speed

    @property
    def position(self) -> float:  # type: ignore[override]
        with self._lock:
            return min(self._pos(), self.duration or 0.0)

    def play(self, url: str) -> None:
        with self._lock:
            self.url = url
            self.duration = self.default_length
            self._offset = 0.0
            self._started = self._clock()
            self.paused = False
            self._finished = None

    def stop(self) -> None:
        with self._lock:
            self.url = None
            self._started = None
            self._offset = 0.0

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            if paused and self._started is not None:
                self._offset = self._pos()
                self._started = None
            elif not paused and self.url and self._started is None:
                self._started = self._clock()
            self.paused = paused

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(100, volume))

    def seek(self, seconds: float) -> None:
        with self._lock:
            self._offset = max(0.0, self._pos() + seconds)
            if self._started is not None:
                self._started = self._clock()

    def _check_end(self) -> None:
        if self.url and self.duration is not None and self._pos() >= self.duration:
            self.url = None
            self._started = None
            self._finished = "eof"

    def is_busy(self) -> bool:
        with self._lock:
            self._check_end()
            return self.url is not None

    def take_finished(self) -> str | None:
        with self._lock:
            self._check_end()
            reason, self._finished = self._finished, None
            return reason


def ytdl_account_args(cfg: dict) -> list[str]:
    """mpv args that make yt-dlp stream as the user's YouTube account (needed
    for Premium audio quality). Uses a logged-in browser's cookies or an
    exported cookies.txt; with neither, playback is anonymous."""
    if cfg.get("cookies_file"):
        path = os.path.expanduser(cfg["cookies_file"])
        return [f"--ytdl-raw-options-append=cookies={path}"]
    if cfg.get("cookies_from_browser"):
        return [f"--ytdl-raw-options-append=cookies-from-browser={cfg['cookies_from_browser']}"]
    return []


def create_player(cfg: dict, simulate: bool = False) -> tuple[Player, str | None]:
    """Build the configured player. Returns (player, warning)."""
    volume = int(cfg.get("volume", 80))
    if simulate:
        p = SimulatedPlayer()
        p.set_volume(volume)
        return p, None
    mpv = find_mpv(cfg.get("mpv_path"))
    if not mpv:
        p = SimulatedPlayer()
        return p, "mpv not found - running silent. Install mpv to hear music."
    try:
        return MpvPlayer(mpv, find_ytdlp(cfg.get("ytdlp_path")), volume=volume,
                         audio_device=cfg.get("audio_device"),
                         extra_args=ytdl_account_args(cfg) + list(cfg.get("mpv_args") or [])), None
    except Exception as exc:  # pragma: no cover - environment specific
        log.exception("Failed to start mpv")
        return SimulatedPlayer(), f"Audio unavailable ({exc}) - running silent."
