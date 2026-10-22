"""The cabinet has no keyboard, so JukeTube must always exit after Coin + Start,
or Attract-Mode never returns. These run the real entry point in a subprocess."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(script: str, timeout: float = 30) -> tuple[subprocess.CompletedProcess, float]:
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
               PYGAME_HIDE_SUPPORT_PROMPT="1")
    start = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)
    return proc, time.monotonic() - start


def test_pygame_audio_is_never_started():
    proc, _ = run("""
        import pygame
        from jukebox.__main__ import init_pygame
        init_pygame()
        print(pygame.display.get_init(), pygame.font.get_init(), pygame.joystick.get_init(),
              pygame.mixer.get_init())
    """)
    assert proc.stdout.split() == ["True", "True", "True", "None"], proc.stderr


def test_exit_does_not_wait_for_a_hung_pygame_quit():
    # Simulate the 2026-10-22 deadlock: pygame.quit() never returns.
    proc, elapsed = run("""
        import threading, pygame
        from jukebox import __main__ as m
        m.EXIT_TIMEOUT = 0.5
        pygame.quit = lambda: threading.Event().wait()
        m.finish(restart=False)
        print("not reached")
    """)
    assert proc.returncode == 0
    assert "not reached" not in proc.stdout
    assert elapsed < 10


def test_exit_does_not_wait_for_busy_worker_threads():
    # A network worker stuck in a slow request must not delay the exit.
    proc, elapsed = run("""
        import time
        from concurrent.futures import ThreadPoolExecutor
        from jukebox import __main__ as m
        ThreadPoolExecutor(1).submit(time.sleep, 60)
        m.finish(restart=False)
    """)
    assert proc.returncode == 0
    assert elapsed < 10
