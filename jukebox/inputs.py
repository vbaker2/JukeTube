"""Maps keyboard / joystick events to abstract jukebox actions.

Arcade controls arrive either as keyboard keys (keyboard encoders) or as
joystick axes/hats/buttons (USB encoders). Both are translated into the same
Action values, and held directions auto-repeat regardless of where they came
from so scrolling long lists with a stick feels right.
"""

from __future__ import annotations

import enum
import logging

import pygame

log = logging.getLogger(__name__)


class Action(enum.Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    SELECT = "select"
    BACK = "back"
    ALT = "alt"
    SKIP = "skip"
    PAUSE = "pause"
    START = "start"
    COIN = "coin"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    EXIT = "exit"


DIRECTIONS = (Action.UP, Action.DOWN, Action.LEFT, Action.RIGHT)
REPEATABLE = DIRECTIONS + (Action.VOLUME_UP, Action.VOLUME_DOWN)


class InputMapper:
    def __init__(self, controls: dict):
        self.repeat_delay = float(controls.get("repeat_delay", 0.35))
        self.repeat_rate = float(controls.get("repeat_rate", 0.07))
        self.keys: dict[int, Action] = {}
        for name, keys in (controls.get("keyboard") or {}).items():
            action = self._action(name)
            if action is None:
                continue
            for key_name in keys if isinstance(keys, list) else [keys]:
                try:
                    self.keys[pygame.key.key_code(str(key_name))] = action
                except ValueError:
                    log.warning("Unknown key name in config: %r", key_name)
        joy = controls.get("joystick") or {}
        self.deadzone = float(joy.get("deadzone", 0.5))
        self.buttons: dict[int, Action] = {}
        for name, buttons in joy.items():
            action = self._action(name)
            if action is None:
                continue
            for b in buttons if isinstance(buttons, list) else [buttons]:
                self.buttons[int(b)] = action
        # held action -> (source id, next repeat time)
        self._held: dict[tuple, float] = {}
        self._axis_state: dict[tuple, Action | None] = {}
        self._hat_state: dict[tuple, set[Action]] = {}
        self.joysticks: dict[int, pygame.joystick.JoystickType] = {}
        self.text_input: list[str] = []  # typed characters for search
        self.typing = False

    @staticmethod
    def _action(name: str) -> Action | None:
        try:
            return Action(name)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    def _press(self, src: tuple, action: Action, now: float, out: list[Action]) -> None:
        out.append(action)
        if action in REPEATABLE:
            self._held[(src, action)] = now + self.repeat_delay

    def _release(self, src: tuple, action: Action) -> None:
        self._held.pop((src, action), None)

    def handle(self, event: pygame.event.Event, now: float) -> list[Action]:
        out: list[Action] = []
        et = event.type
        if et == pygame.KEYDOWN:
            action = self.keys.get(event.key)
            if self.typing and event.unicode and event.unicode.isprintable():
                # Printable keys type into the search box (via TEXTINPUT)
                # instead of triggering whatever button they're mapped to.
                action = None
            elif self.typing and event.key == pygame.K_BACKSPACE:
                self.text_input.append("\b")
                action = None
            if action:
                self._press(("key", event.key), action, now, out)
        elif et == pygame.KEYUP:
            action = self.keys.get(event.key)
            if action:
                self._release(("key", event.key), action)
        elif et == pygame.TEXTINPUT:
            if self.typing:
                self.text_input.append(event.text)
        elif et == pygame.JOYBUTTONDOWN:
            action = self.buttons.get(event.button)
            if action:
                self._press(("btn", event.instance_id, event.button), action, now, out)
        elif et == pygame.JOYBUTTONUP:
            action = self.buttons.get(event.button)
            if action:
                self._release(("btn", event.instance_id, event.button), action)
        elif et == pygame.JOYAXISMOTION and event.axis in (0, 1):
            key = (event.instance_id, event.axis)
            neg, pos = (Action.LEFT, Action.RIGHT) if event.axis == 0 else (Action.UP, Action.DOWN)
            new = neg if event.value < -self.deadzone else pos if event.value > self.deadzone else None
            old = self._axis_state.get(key)
            if new != old:
                if old:
                    self._release(("axis",) + key, old)
                if new:
                    self._press(("axis",) + key, new, now, out)
                self._axis_state[key] = new
        elif et == pygame.JOYHATMOTION:
            key = (event.instance_id, event.hat)
            x, y = event.value
            new = set()
            if x < 0:
                new.add(Action.LEFT)
            elif x > 0:
                new.add(Action.RIGHT)
            if y > 0:
                new.add(Action.UP)
            elif y < 0:
                new.add(Action.DOWN)
            old = self._hat_state.get(key, set())
            for a in old - new:
                self._release(("hat",) + key, a)
            for a in new - old:
                self._press(("hat",) + key, a, now, out)
            self._hat_state[key] = new
        elif et == pygame.JOYDEVICEADDED:
            js = pygame.joystick.Joystick(event.device_index)
            self.joysticks[js.get_instance_id()] = js
            log.info("Joystick connected: %s (%d buttons)", js.get_name(), js.get_numbuttons())
        elif et == pygame.JOYDEVICEREMOVED:
            self.joysticks.pop(event.instance_id, None)
            for held in [h for h in self._held if h[0][0] != "key" and h[0][1] == event.instance_id]:
                self._held.pop(held, None)
        return out

    def repeats(self, now: float) -> list[Action]:
        out = []
        for key, due in list(self._held.items()):
            if now >= due:
                out.append(key[1])
                self._held[key] = max(due + self.repeat_rate, now)
        return out

    def take_text(self) -> str:
        text = "".join(self.text_input)
        self.text_input.clear()
        return text
