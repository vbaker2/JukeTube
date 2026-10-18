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
    PROFILE_NEXT = "profile_next"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    EXIT = "exit"
    QUIT = "quit"  # deliberate held cabinet gesture; asks before closing


Slot = tuple[str, int]  # (joystick guid, ordinal among connected devices with that guid)


def calibration_slots(positions: dict) -> dict[str, Slot]:
    """Stable slots for each calibrated button position.

    Records written by older calibrations only carry an SDL instance id, which
    shifts between boots. Two identical DragonRise boards share a guid, so a
    board is identified by its rank among same-guid devices: the lower instance
    id at calibration time is ordinal 0, which SDL enumerates first again."""
    by_guid: dict[str, set[int]] = {}
    for rec in positions.values():
        if isinstance(rec.get("instance_id"), int):
            by_guid.setdefault(str(rec.get("guid", "")), set()).add(rec["instance_id"])
    slots = {}
    for pos, rec in positions.items():
        guid = str(rec.get("guid", ""))
        if isinstance(rec.get("ordinal"), int):
            slots[pos] = (guid, rec["ordinal"])
        elif isinstance(rec.get("instance_id"), int):
            slots[pos] = (guid, sorted(by_guid[guid]).index(rec["instance_id"]))
    return slots


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
        # Optional per-encoder overrides are installed from cabinet calibration.
        # They're stored against a stable *slot* (guid, ordinal among devices
        # with that guid) because SDL instance ids depend on enumeration order:
        # the UltraStik connecting first shifts both DragonRise panels.
        self._slot_buttons: dict[tuple[Slot, int], Action] = {}
        self._slot_chords: dict[tuple[Slot, tuple[int, int]], Action] = {}
        self._slots: dict[int, Slot] = {}  # live instance id -> slot
        # Derived from the above for the devices currently connected.
        self.device_buttons: dict[tuple[int, int], Action] = {}
        # Calibrated button chords (device, sorted button pair) -> action.
        self.device_chords: dict[tuple[int, tuple[int, int]], Action] = {}
        self._chord_pending: dict[tuple[int, int], float] = {}
        self._chord_used: set[tuple[int, int]] = set()
        # held action -> (source id, next repeat time)
        self._held: dict[tuple, float] = {}
        self._axis_state: dict[tuple, Action | None] = {}
        self._hat_state: dict[tuple, set[Action]] = {}
        self.joysticks: dict[int, pygame.joystick.JoystickType] = {}
        self.text_input: list[str] = []  # typed characters for search
        self.typing = False
        self.exit_hold = float(controls.get("exit_hold_seconds", 1.0))
        self._cabinet_buttons: dict[int, set[int]] = {}
        self._exit_started: dict[int, float] = {}
        self._exit_used: set[int] = set()

    def set_device_button_action(self, slot: Slot, button: int, action: Action) -> None:
        self._slot_buttons[(slot, int(button))] = action
        self._rebuild_device_maps()

    def set_device_chord(self, slot: Slot, buttons: tuple[int, int], action: Action) -> None:
        pair = tuple(sorted((int(buttons[0]), int(buttons[1]))))
        self._slot_chords[(slot, pair)] = action
        self._rebuild_device_maps()

    def instance_for(self, slot: Slot) -> int | None:
        return next((i for i, s in self._slots.items() if s == slot), None)

    def add_device(self, instance_id: int, guid: str) -> None:
        """Register a connected joystick; it takes the next ordinal for its guid."""
        ordinal = sum(1 for s in self._slots.values() if s[0] == guid)
        self._slots[instance_id] = (guid, ordinal)
        self._rebuild_device_maps()

    def remove_device(self, instance_id: int) -> None:
        self._slots.pop(instance_id, None)
        # Re-number the remaining devices of each guid in connection order.
        by_guid: dict[str, list[int]] = {}
        for inst in sorted(self._slots):
            by_guid.setdefault(self._slots[inst][0], []).append(inst)
        self._slots = {inst: (guid, n) for guid, insts in by_guid.items() for n, inst in enumerate(insts)}
        self._rebuild_device_maps()

    def _rebuild_device_maps(self) -> None:
        self.device_buttons = {(inst, b): a for (slot, b), a in self._slot_buttons.items()
                               if (inst := self.instance_for(slot)) is not None}
        self.device_chords = {(inst, pair): a for (slot, pair), a in self._slot_chords.items()
                              if (inst := self.instance_for(slot)) is not None}

    def slot_button_action(self, slot: Slot | None, button: int) -> Action | None:
        """What a calibrated button does, whether or not its panel is connected."""
        return self._slot_buttons.get((slot, button), self.buttons.get(button))

    def button_action(self, instance_id: int, button: int) -> Action | None:
        return self.device_buttons.get((instance_id, button), self.buttons.get(button))

    def _exit_roles(self, instance_id: int, button: int) -> set[Action]:
        """Which halves of the Coin + Start quit gesture this button counts as.

        The plain button numbers from the config (Coin 8, Start 9) always count,
        whatever calibration says, so the cabinet can never be left without a
        way back to Attract-Mode. The right-hand coin button changes profiles
        on a tap, but still counts as Coin when held with Start."""
        roles = set()
        for action in (self.button_action(instance_id, button), self.buttons.get(button)):
            if action == Action.PROFILE_NEXT:
                action = Action.COIN
            if action in (Action.COIN, Action.START):
                roles.add(action)
        return roles

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
            action = self.button_action(event.instance_id, event.button)
            if self._exit_roles(event.instance_id, event.button):
                held = self._cabinet_buttons.setdefault(event.instance_id, set())
                held.add(event.button)
                roles = set().union(*(self._exit_roles(event.instance_id, b) for b in held))
                if roles >= {Action.COIN, Action.START}:
                    self._exit_started.setdefault(event.instance_id, now)
                return out
            chord_key = (event.instance_id, event.button)
            pairs = [((device, pair), mapped) for (device, pair), mapped in self.device_chords.items()
                     if device == event.instance_id and event.button in pair]
            if pairs:
                self._chord_pending[chord_key] = now
                held = self._cabinet_buttons.setdefault(event.instance_id, set())
                held.add(event.button)
                for (device, pair), mapped in pairs:
                    if set(pair).issubset(held):
                        self._chord_used.update((device, b) for b in pair)
                        self._chord_pending.pop((device, pair[0]), None)
                        self._chord_pending.pop((device, pair[1]), None)
                        out.append(mapped)
                        break
                return out
            if action:
                self._press(("btn", event.instance_id, event.button), action, now, out)
        elif et == pygame.JOYBUTTONUP:
            action = self.button_action(event.instance_id, event.button)
            if self._exit_roles(event.instance_id, event.button):
                device = event.instance_id
                held = self._cabinet_buttons.get(device, set())
                if event.button in held:
                    # A short chord is cancelled; single taps retain normal behaviour.
                    chord = device in self._exit_started or device in self._exit_used
                    if chord:
                        self._exit_used.add(device)
                    elif action:
                        out.append(action)
                    held.discard(event.button)
                    self._exit_started.pop(device, None)
                    if not held:
                        self._cabinet_buttons.pop(device, None)
                        self._exit_used.discard(device)
                return out
            chord_key = (event.instance_id, event.button)
            chord_button = any(device == event.instance_id and event.button in pair
                               for device, pair in self.device_chords)
            was_chord = chord_key in self._chord_used
            self._chord_used.discard(chord_key)
            self._chord_pending.pop(chord_key, None)
            held = self._cabinet_buttons.get(event.instance_id, set())
            held.discard(event.button)
            if not held:
                self._cabinet_buttons.pop(event.instance_id, None)
            if was_chord:
                return out
            if chord_button:
                if action:
                    out.append(action)
                return out
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
            self.add_device(js.get_instance_id(), js.get_guid())
            log.info("Joystick connected: %s (%d buttons) as instance %d, slot %s",
                     js.get_name(), js.get_numbuttons(), js.get_instance_id(),
                     self._slots[js.get_instance_id()][1])
        elif et == pygame.JOYDEVICEREMOVED:
            self.joysticks.pop(event.instance_id, None)
            self.remove_device(event.instance_id)
            log.info("Joystick disconnected: instance %d", event.instance_id)
            self._cabinet_buttons.pop(event.instance_id, None)
            self._chord_pending = {k: v for k, v in self._chord_pending.items() if k[0] != event.instance_id}
            self._chord_used = {k for k in self._chord_used if k[0] != event.instance_id}
            self._exit_started.pop(event.instance_id, None)
            self._exit_used.discard(event.instance_id)
            self._axis_state = {k: v for k, v in self._axis_state.items() if k[0] != event.instance_id}
            self._hat_state = {k: v for k, v in self._hat_state.items() if k[0] != event.instance_id}
            for held in [h for h in self._held if h[0][0] != "key" and h[0][1] == event.instance_id]:
                self._held.pop(held, None)
        return out

    def repeats(self, now: float) -> list[Action]:
        out = []
        for device, started in self._exit_started.items():
            if device not in self._exit_used and now - started >= self.exit_hold:
                self._exit_used.add(device)
                out.append(Action.QUIT)
        for key, due in list(self._held.items()):
            if now >= due:
                out.append(key[1])
                self._held[key] = max(due + self.repeat_rate, now)
        return out

    def take_text(self) -> str:
        text = "".join(self.text_input)
        self.text_input.clear()
        return text
