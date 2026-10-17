"""Test GitHub releases off-thread and apply them only at a quiet boundary.

The updater is opt-in at launch (``JUKEBOX_AUTO_UPDATE=1``), so development
runs never contact GitHub. On the cabinet it uses a repository-scoped SSH
deploy key, stages the current ``master`` commit, and runs the full test suite
before exposing it to the UI for installation.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

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
        self._retry_after = 0.0
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
        if not commit or commit == self._installed_commit():
            return
        if self.stage.exists():
            shutil.rmtree(self.stage)
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", "master",
                        remote, str(self.stage)], env=env, capture_output=True,
                       text=True, timeout=180, check=True)
        actual = subprocess.run(["git", "-C", str(self.stage), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        if actual != commit:
            raise RuntimeError("GitHub branch changed while staging; retrying next poll")
        test = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=self.stage,
                              capture_output=True, text=True, timeout=900)
        if test.returncode:
            log.error("Update %s failed cabinet tests:\n%s\n%s", commit[:12],
                      test.stdout[-5000:], test.stderr[-5000:])
            return
        log.info("Update %s passed cabinet tests and is ready", commit[:12])
        with self._lock:
            self._ready = commit

    def _installed_commit(self) -> str:
        try:
            return (self.state_dir / "installed-commit").read_text().strip()
        except OSError:
            return ""

    def hold_at_boundary(self) -> bool:
        """Called by the playback engine; true holds at idle while installing."""
        if time.monotonic() < self._retry_after:
            return False
        with self._lock:
            commit = self._ready
            if commit is None:
                return False
            self._ready = None
        try:
            self._announce()
            self._install(commit)
            log.info("Installed update %s; restarting JukeTube in place", commit[:12])
            return True
        except Exception:
            log.exception("Update install failed; keeping the current app running")
            self._retry_after = time.monotonic() + 30
            with self._lock:
                self._ready = commit
            return False

    def _announce(self) -> None:
        speaker = shutil.which("espeak-ng") or shutil.which("espeak")
        mpv = shutil.which("mpv")
        if not speaker or not mpv:
            raise RuntimeError("voice announcement needs espeak-ng and mpv")
        with tempfile.TemporaryDirectory(prefix="juketube-update-voice-") as tmp:
            wav = Path(tmp) / "updating.wav"
            subprocess.run([speaker, "-w", str(wav), "Updating software"],
                           check=True, timeout=15)
            subprocess.run([mpv, "--no-video", "--really-quiet", str(wav)],
                           check=True, timeout=20)

    def _install(self, commit: str) -> None:
        # Only repository files are copied. Cabinet credentials, config,
        # favourites, the virtualenv and Attract's launcher stay in place.
        listed = subprocess.run(["git", "-C", str(self.stage), "ls-files", "-z"],
                                capture_output=True, check=True).stdout.split(b"\0")
        backup = Path(tempfile.mkdtemp(prefix="juketube-update-backup-",
                                       dir=self.state_dir))
        copied: list[Path] = []
        try:
            self._copy_release_files(listed, backup, copied)
            (self.state_dir / "installed-commit").write_text(commit + "\n")
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

