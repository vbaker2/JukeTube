"""Test GitHub releases off-thread and apply them only at a quiet boundary.

The updater is opt-in at launch (``JUKEBOX_AUTO_UPDATE=1``), so development
runs never contact GitHub. On the cabinet it uses a repository-scoped SSH
deploy key, stages the current ``master`` commit, and runs the full test suite
before exposing it to the UI for installation.
"""

from __future__ import annotations

import logging
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from dataclasses import asdict

from .engine import QueueEntry
from .models import Track

log = logging.getLogger(__name__)


class UpdateAgent:
    def __init__(self, project: Path, state_dir: Path, interval: int = 300):
        self.project = project.resolve()
        self.state_dir = state_dir
        self.interval = max(60, interval)
        self.stage = state_dir / "staged-release"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ready: str | None = None
        self._applying = False
        self._retry_after = 0.0
        self._failed_commit: str | None = None
        self._engine = None  # set by restore_queue; lets an update keep credits
        self._stopped = threading.Event()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        self._worker = threading.Thread(target=self._poll_loop, name="updates", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stopped.set()

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready is not None

    def _poll_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self._stage_latest()
            except Exception:
                log.exception("Update check failed; the installed version is unchanged")
            self._stopped.wait(self.interval)

    def _stage_latest(self) -> None:
        with self._lock:
            if self._ready is not None or self._applying:
                return
        key = Path.home() / ".ssh" / "juketube_deploy"
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
            "-o StrictHostKeyChecking=yes"
        )
        remote = "git@github.com:vbaker2/JukeTube.git"
        result = subprocess.run(
            ["git", "ls-remote", remote, "refs/heads/master"], env=env,
            capture_output=True, text=True, timeout=30, check=True,
        )
        commit = result.stdout.split()[0]
        if not commit or commit == self._installed_commit() or commit == self._failed_commit:
            return  # nothing new, or already known to fail the cabinet tests
        if self.stage.exists():
            shutil.rmtree(self.stage)
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", "master",
                        remote, str(self.stage)], env=env, capture_output=True,
                       text=True, timeout=180, check=True)
        actual = subprocess.run(["git", "-C", str(self.stage), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        if actual != commit:
            raise RuntimeError("GitHub branch changed while staging; retrying next poll")
        test_env = os.environ.copy()
        test_env["SDL_VIDEODRIVER"] = "dummy"
        test_env["SDL_AUDIODRIVER"] = "dummy"
        test = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=self.stage,
                              env=test_env,
                              capture_output=True, text=True, timeout=900)
        if test.returncode:
            log.error("Update %s failed cabinet tests:\n%s\n%s", commit[:12],
                      test.stdout[-5000:], test.stderr[-5000:])
            self._failed_commit = commit  # don't re-clone and re-test it every poll
            return
        log.info("Update %s passed cabinet tests and is ready", commit[:12])
        with self._lock:
            self._ready = commit

    def _installed_commit(self) -> str:
        try:
            return (self.state_dir / "installed-commit").read_text().strip()
        except OSError:
            return ""

    def restore_queue(self, engine) -> None:
        """Restore queued songs (and credits) saved immediately before an in-place update."""
        self._engine = engine
        credits_path = self.state_dir / "update-credits.json"
        try:
            saved = int(json.loads(credits_path.read_text()))
            engine.credits = max(engine.credits, saved)
            credits_path.unlink()
            log.info("Restored %d credit(s) after software update", engine.credits)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            log.error("Couldn't restore credits after update: %s", exc)
        path = self.state_dir / "update-queue.json"
        try:
            entries = json.loads(path.read_text())
            restored = [QueueEntry(Track(**item["track"]), bool(item["requested"]))
                        for item in entries]
            engine.queue.extend(restored)
            path.unlink()
            if restored:
                log.info("Restored %d queued song(s) after software update", len(restored))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError) as exc:
            log.error("Couldn't restore queued songs after update: %s", exc)

    def hold_at_boundary(self, queue: list[QueueEntry]) -> bool:
        """Install after the current track; persist the remaining queue first."""
        if time.monotonic() < self._retry_after:
            return False
        with self._lock:
            commit = self._ready
            if commit is None:
                return False
            self._ready = None
            self._applying = True
        try:
            self._announce()
            self._install(commit, queue)
            log.info("Installed update %s; restarting JukeTube in place", commit[:12])
            return True
        except Exception:
            log.exception("Update install failed; keeping the current app running")
            self._retry_after = time.monotonic() + 30
            with self._lock:
                self._ready = commit
            return False
        finally:
            with self._lock:
                self._applying = False

    def _announce(self) -> None:
        """Say "Updating software". Best effort: it runs on the UI thread just
        before the restart, so it must be quick and must never block an install."""
        speaker = shutil.which("espeak-ng") or shutil.which("espeak")
        mpv = shutil.which("mpv")
        if not speaker or not mpv:
            log.info("Skipping update announcement (needs espeak-ng and mpv)")
            return
        try:
            wav = self.state_dir / "updating.wav"
            subprocess.run([speaker, "-w", str(wav), "Updating software"],
                           check=True, timeout=5, capture_output=True)
            # Detached, so it keeps talking while JukeTube restarts.
            subprocess.Popen([mpv, "--no-video", "--really-quiet", str(wav)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Update announcement failed: %s", exc)

    def _install(self, commit: str, queue: list[QueueEntry]) -> None:
        # Only repository files are copied. Cabinet credentials, config,
        # favourites, the virtualenv and Attract's launcher stay in place.
        listed = subprocess.run(["git", "-C", str(self.stage), "ls-files", "-z"],
                                capture_output=True, check=True).stdout.split(b"\0")
        backup = Path(tempfile.mkdtemp(prefix="juketube-update-backup-",
                                       dir=self.state_dir))
        copied: list[Path] = []
        marker = self.state_dir / "installed-commit"
        old_marker = marker.read_text() if marker.exists() else None
        queue_path = self.state_dir / "update-queue.json"
        try:
            self._copy_release_files(listed, backup, copied)
            pending = [{"track": asdict(entry.track), "requested": entry.requested}
                       for entry in queue]
            tmp_queue = self.state_dir / "update-queue.tmp"
            tmp_queue.write_text(json.dumps(pending))
            os.replace(tmp_queue, queue_path)
            if self._engine is not None and self._engine.credits:
                (self.state_dir / "update-credits.json").write_text(str(self._engine.credits))
            tmp_marker = self.state_dir / "installed-commit.tmp"
            tmp_marker.write_text(commit + "\n")
            os.replace(tmp_marker, marker)
        except Exception:
            log.exception("Copying release files failed; restoring previous files")
            for rel in copied:
                old = backup / rel
                dest = self.project / rel
                if old.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(old, dest)
                else:
                    dest.unlink(missing_ok=True)
            queue_path.unlink(missing_ok=True)
            if old_marker is None:
                marker.unlink(missing_ok=True)
            else:
                marker.write_text(old_marker)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)
        # The caller handles the in-place exec after normal pygame/mpv
        # shutdown has run.
        os.environ["JUKEBOX_UPDATE_RESTART"] = "1"

    def _copy_release_files(self, listed: list[bytes], backup: Path,
                            copied: list[Path]) -> None:
        for raw in listed:
            if not raw:
                continue
            rel = Path(os.fsdecode(raw))
            if rel.parts[0] in {".git", ".venv", "games"}:
                continue
            src = self.stage / rel
            dest = self.project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            old = backup / rel
            if dest.exists():
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, old)
            copied.append(rel)
            shutil.copy2(src, dest)

