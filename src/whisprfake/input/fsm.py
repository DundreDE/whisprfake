"""Hotkey state machine, independent of evdev so it can be unit-tested.

Mirrors Wispr Flow's behaviour:
  * hold the push-to-talk chord            -> record while held, process on release
  * double-tap the chord / hands-free chord -> locked hands-free recording, stop with chord again
  * add ALT to the held chord               -> command mode
  * ESC while recording                     -> cancel
  * any other key while the chord is held   -> it was a compositor shortcut (e.g. Ctrl+Super+X): drop silently
Recording starts immediately (no lost first syllable) but user-visible feedback is delayed by
`grace_ms`, so Omarchy shortcuts starting with Ctrl+Super never flash the Flow Bar or play a sound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

MODIFIER_ALIASES = {
    "LEFTCTRL": "CTRL", "RIGHTCTRL": "CTRL",
    "LEFTMETA": "SUPER", "RIGHTMETA": "SUPER",
    "LEFTALT": "ALT", "RIGHTALT": "ALT",
    "LEFTSHIFT": "SHIFT", "RIGHTSHIFT": "SHIFT",
    "ESCAPE": "ESC",
}
MODIFIERS = {"CTRL", "SUPER", "ALT", "SHIFT"}


def normalize_key(name: str) -> str:
    name = name.upper().removeprefix("KEY_").removeprefix("BTN_")
    return MODIFIER_ALIASES.get(name, name)


def parse_chord(chord: str) -> frozenset[str]:
    aliases = {"WIN": "SUPER", "META": "SUPER", "CONTROL": "CTRL", "ESCAPE": "ESC"}
    return frozenset(aliases.get(p.strip().upper(), p.strip().upper()) for p in chord.split("+") if p.strip())


class Action(Enum):
    PREWARM = auto()           # a chord modifier went down: open the mic early
    START = auto()             # begin capturing (silently)
    CONFIRM = auto()           # grace period passed: show Flow Bar, play start sound
    UPGRADE_COMMAND = auto()   # switch the running session to command mode
    LOCK = auto()              # switch to hands-free
    STOP = auto()              # finish and process
    CANCEL = auto()            # user cancelled (ESC): discard, play cancel sound
    DISCARD = auto()           # silent drop (shortcut collision / lone tap)
    PASTE_LAST = auto()


@dataclass
class Emit:
    action: Action
    mode: str | None = None    # "dictate" | "command" for START


class S(Enum):
    IDLE = auto()
    HOLD = auto()
    TAP_WAIT = auto()
    LOCKED = auto()
    WAIT_RELEASE = auto()


@dataclass
class HotkeyFSM:
    ptt: list[frozenset[str]]
    hands_free: list[frozenset[str]]
    command: list[frozenset[str]]
    paste_last: list[frozenset[str]] = field(default_factory=list)
    cancel: str = "ESC"
    tap_max_ms: int = 250
    double_tap_ms: int = 500
    grace_ms: int = 150

    state: S = S.IDLE
    pressed: set[str] = field(default_factory=set)
    t_start: float = 0.0
    t_release: float = 0.0
    confirmed: bool = False
    armed: bool = False        # LOCKED: chord was released once, next chord press stops
    mode: str = "dictate"

    @classmethod
    def from_config(cls, sc) -> "HotkeyFSM":
        return cls(
            ptt=[parse_chord(c) for c in sc.push_to_talk],
            hands_free=[parse_chord(c) for c in sc.hands_free],
            command=[parse_chord(c) for c in sc.command],
            paste_last=[parse_chord(c) for c in sc.paste_last],
            cancel=normalize_key(sc.cancel),
            tap_max_ms=sc.tap_max_ms,
            double_tap_ms=sc.double_tap_ms,
            grace_ms=sc.grace_ms,
        )

    # ---- helpers -------------------------------------------------------
    def _is(self, chords: list[frozenset[str]]) -> bool:
        return any(self.pressed == c for c in chords)

    def _holds_ptt(self) -> bool:
        return any(c <= self.pressed for c in self.ptt + self.command)

    def _chord_keys(self) -> set[str]:
        keys: set[str] = set()
        for c in self.ptt + self.hands_free + self.command + self.paste_last:
            keys |= c
        return keys

    def _start(self, now: float, mode: str) -> list[Emit]:
        self.state, self.t_start, self.confirmed, self.mode = S.HOLD, now, False, mode
        return [Emit(Action.START, mode)]

    # ---- events --------------------------------------------------------
    def key(self, name: str, down: bool, now: float) -> list[Emit]:
        """Feed a key event. `now` is seconds (monotonic). Repeats (value 2) must be filtered by caller."""
        k = normalize_key(name)
        out: list[Emit] = []
        if down:
            if k in self.pressed:
                return out
            self.pressed.add(k)
            out += self._down(k, now)
        else:
            self.pressed.discard(k)
            out += self._up(k, now)
        return out

    def _down(self, k: str, now: float) -> list[Emit]:
        st = self.state
        if st is S.IDLE:
            if self._is(self.paste_last):
                self.state = S.WAIT_RELEASE
                return [Emit(Action.PASTE_LAST)]
            if self._is(self.hands_free):
                out = self._start(now, "dictate")
                self.state, self.armed = S.LOCKED, False
                return out + [Emit(Action.CONFIRM), Emit(Action.LOCK)]
            if self._is(self.command):
                return self._start(now, "command")
            if self._is(self.ptt):
                return self._start(now, "dictate")
            if k in MODIFIERS and k in self._chord_keys():
                return [Emit(Action.PREWARM)]
            return []

        if st is S.HOLD:
            if k == self.cancel:
                self.state = S.WAIT_RELEASE
                return [Emit(Action.CANCEL) if self.confirmed else Emit(Action.DISCARD)]
            if self._is(self.hands_free):
                self.state, self.armed = S.LOCKED, False
                pre = [] if self.confirmed else [Emit(Action.CONFIRM)]
                self.confirmed = True
                return pre + [Emit(Action.LOCK)]
            if self._is(self.paste_last):
                self.state = S.WAIT_RELEASE
                return [Emit(Action.DISCARD), Emit(Action.PASTE_LAST)]
            if self._is(self.command):
                if self.mode != "command":
                    self.mode = "command"
                    return [Emit(Action.UPGRADE_COMMAND)]
                return []
            if k in MODIFIERS:
                return []
            # Some other key joined the chord: it was a compositor shortcut.
            self.state = S.WAIT_RELEASE
            return [Emit(Action.DISCARD)]

        if st is S.TAP_WAIT:
            if self._is(self.ptt) or self._is(self.hands_free):
                self.state, self.armed = S.LOCKED, False
                self.confirmed = True
                return [Emit(Action.CONFIRM), Emit(Action.LOCK)]
            if k in MODIFIERS and k in self._chord_keys():
                return []
            self.state = S.IDLE if not self.pressed - {k} else S.WAIT_RELEASE
            return [Emit(Action.DISCARD)]

        if st is S.LOCKED:
            if k == self.cancel:
                self.state = S.IDLE if not (self.pressed - {k}) else S.WAIT_RELEASE
                return [Emit(Action.CANCEL)]
            if self.armed and (self._is(self.ptt) or self._is(self.hands_free)):
                self.state = S.WAIT_RELEASE
                return [Emit(Action.STOP)]
            return []

        return []

    def _up(self, k: str, now: float) -> list[Emit]:
        st = self.state
        if st is S.HOLD and not self._holds_ptt():
            held_ms = (now - self.t_start) * 1000
            if held_ms < self.tap_max_ms and self.mode == "dictate":
                self.state, self.t_release = S.TAP_WAIT, now
                return []
            self.state = S.IDLE
            if not self.confirmed:
                # Released before the grace period ended but longer than a tap: still a real dictation.
                self.confirmed = True
                return [Emit(Action.CONFIRM), Emit(Action.STOP)]
            return [Emit(Action.STOP)]
        if st is S.LOCKED and not self._holds_ptt() and not any(c <= self.pressed for c in self.hands_free):
            self.armed = True
            return []
        if st is S.WAIT_RELEASE and not (self.pressed & self._chord_keys()):
            self.state = S.IDLE
        return []

    def tick(self, now: float) -> list[Emit]:
        """Call periodically (~every 20-50 ms)."""
        if self.state is S.HOLD and not self.confirmed and (now - self.t_start) * 1000 >= self.grace_ms:
            self.confirmed = True
            return [Emit(Action.CONFIRM)]
        if self.state is S.TAP_WAIT and (now - self.t_release) * 1000 >= self.double_tap_ms:
            self.state = S.IDLE
            return [Emit(Action.DISCARD)]
        return []

    def external_stop(self) -> None:
        """Session ended from elsewhere (Flow Bar click, time limit, IPC)."""
        self.state = S.WAIT_RELEASE if self.pressed & self._chord_keys() else S.IDLE

    def external_lock(self) -> None:
        """A hands-free session was started from elsewhere (Flow Bar, CLI): next chord press stops it."""
        self.state, self.armed, self.confirmed = S.LOCKED, True, True
