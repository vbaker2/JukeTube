"""Run only with owner permission, while Attract and the jukebox are stopped.

Collect physical button positions without assuming joystick enumeration order.
The owner follows prompts on the cabinet. No music or gameplay actions execute.
"""

import json
from pathlib import Path
import time

import pygame

from jukebox.ui.controls import BUTTON_POSITIONS, BUTTON_PROMPTS, draw_labelled_layout
from jukebox.ui.theme import Theme


def main():
    pygame.init()
    display = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("Jukebox button calibration")
    pygame.mouse.set_visible(False)
    canvas = pygame.Surface((1280, 720))
    theme = Theme()
    joysticks = {}
    positions = list(BUTTON_POSITIONS)
    results = {}
    index = 0
    held = set()
    accept_after = 0
    clock = pygame.time.Clock()
    deadline = time.monotonic() + 600
    try:
        while index < len(positions) and time.monotonic() < deadline:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    return
                if event.type == pygame.JOYDEVICEADDED:
                    joystick = pygame.joystick.Joystick(event.device_index)
                    joysticks[joystick.get_instance_id()] = joystick
                elif event.type == pygame.JOYBUTTONUP:
                    held.discard((event.instance_id, event.button))
                elif event.type == pygame.JOYBUTTONDOWN:
                    key = (event.instance_id, event.button)
                    if not held and time.monotonic() >= accept_after and index < len(positions):
                        position = positions[index]
                        if not any((r["instance_id"], r["button"]) == key for r in results.values()):
                            joystick = joysticks[event.instance_id]
                            # Instance ids shift between boots (e.g. when the UltraStik
                            # enumerates first); the panel's rank among identical boards
                            # is what JukeTube uses to find it again.
                            same = sorted(i for i, j in joysticks.items() if j.get_guid() == joystick.get_guid())
                            results[position] = {"button": event.button, "instance_id": event.instance_id,
                                                 "ordinal": same.index(event.instance_id),
                                                 "name": joystick.get_name(), "guid": joystick.get_guid()}
                            print(position, json.dumps(results[position]), flush=True)
                            index += 1
                            accept_after = time.monotonic() + 0.6
                    held.add(key)
            canvas.fill(theme.c["bg_top"])
            if index >= len(positions):
                break
            canvas.blit(theme.text(f"BUTTON CALIBRATION  {index + 1} / {len(positions)}", 32,
                                   theme.c["accent2"], bold=True), (40, 20))
            canvas.blit(theme.text(BUTTON_PROMPTS[positions[index]], 32, theme.c["text"]), (40, 68))
            canvas.blit(theme.text("Press and release the highlighted button once.", 24,
                                   theme.c["muted"]), (40, 112))
            labels = {p: str(r["button"]) for p, r in results.items()}
            labels[positions[index]] = "PRESS"
            draw_labelled_layout(canvas, pygame.Rect(200, 170, 880, 500), theme, labels, positions[index])
            viewport = canvas.get_rect().fit(display.get_rect())
            display.fill((0, 0, 0))
            display.blit(pygame.transform.smoothscale(canvas, viewport.size), viewport)
            pygame.display.flip()
            clock.tick(30)
        if len(results) == len(positions):
            path = Path.home() / ".config/jukebox/cabinet-buttons.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(results, indent=2) + "\n")
            temporary.replace(path)
            print("SAVED", path, flush=True)
        else:
            print("Calibration timed out; previous settings kept.", flush=True)
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
