"""Bounded, single-key Windows input with a physical emergency stop."""

from __future__ import annotations

import ctypes
import json
import math
import os
import re
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


MIN_HOLD_SECONDS = 0.05
MAX_HOLD_SECONDS = 1.0
EMERGENCY_STOP_LABEL = "Ctrl+Alt+F12"
EMERGENCY_STOP_KEYS = (0x11, 0x12, 0x7B)  # Control, Alt, F12

_KEY_CODES: dict[str, int] = {
    **{chr(code): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(number): ord(str(number)) for number in range(10)},
    "SPACE": 0x20,
    "ENTER": 0x0D,
    "TAB": 0x09,
    "BACKSPACE": 0x08,
    "ESC": 0x1B,
    "PLUS": 0xBB,   # VK_OEM_PLUS
    "MINUS": 0xBD,  # VK_OEM_MINUS
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
}
_ACTION_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")


class KeyboardControlError(RuntimeError):
    """Raised when a safe keyboard action cannot be completed."""


class UnsupportedKeyboardPlatform(KeyboardControlError):
    """Raised when real keyboard input is requested outside Windows."""


class EmergencyStopRequested(KeyboardControlError):
    """Raised when Ctrl+Alt+F12 is pressed during an action."""


class TargetWindowFocusLost(KeyboardControlError):
    """Raised when the emulator window loses foreground focus."""


class KeySender(Protocol):
    def send_key(self, key: str, *, key_down: bool) -> None: ...


class EmergencyStopProbe(Protocol):
    def is_pressed(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class KeyboardMapping:
    window_title_contains: str
    mappings: dict[str, str]


def normalise_key_name(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("Keyboard key names must be strings")
    name = key.strip().upper()
    if name not in _KEY_CODES:
        allowed = ", ".join(sorted(_KEY_CODES))
        raise ValueError(
            f"Unsupported key {key!r}. Use one non-modifier key from: {allowed}. "
            "Ctrl, Alt, Shift, Windows keys and F12 are deliberately unavailable."
        )
    return name


def validate_hold_duration(duration_seconds: float) -> float:
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Hold duration must be a number") from exc
    if not math.isfinite(duration):
        raise ValueError("Hold duration must be finite")
    if duration < MIN_HOLD_SECONDS or duration > MAX_HOLD_SECONDS:
        raise ValueError(
            f"Hold duration must be between {MIN_HOLD_SECONDS:.2f} and "
            f"{MAX_HOLD_SECONDS:.2f} seconds"
        )
    return duration


def load_keyboard_mapping(path: str | Path) -> KeyboardMapping:
    mapping_path = Path(path)
    try:
        raw = json.loads(mapping_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read keyboard mapping file: {mapping_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Keyboard mapping file is not valid JSON: {mapping_path}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Keyboard mapping must be a JSON object")
    unknown = set(raw) - {"window_title_contains", "mappings"}
    if unknown:
        raise ValueError(f"Unknown keyboard mapping fields: {', '.join(sorted(unknown))}")

    title = raw.get("window_title_contains", "Ryujinx")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("window_title_contains must be a non-empty string")

    raw_mappings = raw.get("mappings")
    if not isinstance(raw_mappings, dict) or not raw_mappings:
        raise ValueError("mappings must be a non-empty JSON object")

    mappings: dict[str, str] = {}
    for raw_action, raw_key in raw_mappings.items():
        if not isinstance(raw_action, str):
            raise ValueError("Action names must be strings")
        action = raw_action.strip().upper()
        if not _ACTION_NAME.fullmatch(action):
            raise ValueError(f"Invalid action name: {raw_action!r}")
        if action in mappings:
            raise ValueError(f"Duplicate action name after normalisation: {action}")
        if not isinstance(raw_key, str):
            raise ValueError(f"Key for action {action} must be a string")
        mappings[action] = normalise_key_name(raw_key)

    return KeyboardMapping(window_title_contains=title.strip(), mappings=mappings)


def resolve_action_key(mapping: KeyboardMapping, action: str) -> str:
    if not isinstance(action, str):
        raise ValueError("Action name must be a string")
    name = action.strip().upper()
    if name not in mapping.mappings:
        available = ", ".join(sorted(mapping.mappings))
        raise ValueError(f"Unknown action {name!r}. Configured actions: {available}")
    return mapping.mappings[name]


class WindowsEmergencyStopProbe:
    """Polls the physical Ctrl+Alt+F12 chord using the Windows async key state."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise UnsupportedKeyboardPlatform("Real keyboard control currently requires Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short

    def is_pressed(self) -> bool:
        return all(
            bool(self._user32.GetAsyncKeyState(virtual_key) & 0x8000)
            for virtual_key in EMERGENCY_STOP_KEYS
        )


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    # INPUT contains the complete Win32 union, not just KEYBDINPUT. The
    # MOUSEINPUT member is the largest member and determines cbSize on both
    # 32-bit and 64-bit Windows (28 and 40 bytes respectively for INPUT).
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


class WindowsSendInputSender:
    """Injects one virtual-key down/up event through the Windows SendInput API."""

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002

    def __init__(self) -> None:
        if os.name != "nt":
            raise UnsupportedKeyboardPlatform("Real keyboard control currently requires Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT

    def send_key(self, key: str, *, key_down: bool) -> None:
        name = normalise_key_name(key)
        flags = 0 if key_down else self.KEYEVENTF_KEYUP
        keyboard_input = _KEYBDINPUT(
            wVk=_KEY_CODES[name],
            wScan=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        )
        input_event = _INPUT(type=self.INPUT_KEYBOARD, ki=keyboard_input)
        sent = self._user32.SendInput(1, ctypes.byref(input_event), ctypes.sizeof(_INPUT))
        if sent != 1:
            error = ctypes.get_last_error()
            if error == 87:
                explanation = (
                    f"Windows SendInput rejected the INPUT structure for {name} (error 87: "
                    "ERROR_INVALID_PARAMETER). The ctypes structure size/layout is invalid."
                )
            elif error == 5:
                explanation = (
                    f"Windows SendInput was denied for {name} (error 5: ACCESS_DENIED). "
                    "Check that Ryujinx and this PowerShell process are running at the same privilege level."
                )
            else:
                explanation = f"Windows SendInput failed for {name} (error {error})."
            raise KeyboardControlError(explanation)


class WindowsKeyboardController:
    """Sends one bounded key hold and always attempts a matching key-up."""

    def __init__(
        self,
        *,
        sender: KeySender | None = None,
        emergency_stop: EmergencyStopProbe | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sender = sender or WindowsSendInputSender()
        self._emergency_stop = emergency_stop or WindowsEmergencyStopProbe()
        self._clock = clock
        self._sleep = sleeper
        self._pressed: set[str] = set()

    def press(
        self,
        key: str,
        duration_seconds: float,
        *,
        focus_check: Callable[[], bool],
    ) -> None:
        name = normalise_key_name(key)
        duration = validate_hold_duration(duration_seconds)

        if self._emergency_stop.is_pressed():
            raise EmergencyStopRequested(f"Emergency stop pressed ({EMERGENCY_STOP_LABEL}); no key was sent.")
        if not focus_check():
            raise TargetWindowFocusLost("Ryujinx is not the foreground window; no key was sent.")

        self._sender.send_key(name, key_down=True)
        self._pressed.add(name)
        try:
            deadline = self._clock() + duration
            while True:
                if self._emergency_stop.is_pressed():
                    raise EmergencyStopRequested(
                        f"Emergency stop pressed ({EMERGENCY_STOP_LABEL}); releasing held keys."
                    )
                if not focus_check():
                    raise TargetWindowFocusLost("Ryujinx lost foreground focus; releasing held keys.")
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                self._sleep(min(0.02, remaining))
        finally:
            self.release_all()

    def release_all(self) -> None:
        errors: list[Exception] = []
        for key in tuple(self._pressed):
            try:
                self._sender.send_key(key, key_down=False)
            except Exception as exc:
                errors.append(exc)
            finally:
                self._pressed.discard(key)
        if errors:
            raise KeyboardControlError(
                f"Failed to release {len(errors)} held keyboard key(s); check emulator focus and privilege level."
            ) from errors[0]
