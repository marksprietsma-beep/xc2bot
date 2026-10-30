"""Replaceable interfaces and deterministic mocks for later emulator integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class EmulatorAdapter(Protocol):
    def is_running(self) -> bool: ...


class ScreenCapture(Protocol):
    def capture(self) -> bytes: ...


class ControllerInput(Protocol):
    def press(self, button: str, duration_seconds: float = 0.1) -> None: ...
    def release_all(self) -> None: ...


@dataclass
class MockEmulator:
    running: bool = True

    def is_running(self) -> bool:
        return self.running


@dataclass
class MockScreenCapture:
    frame: bytes = b"mock-frame-not-a-real-screenshot"
    capture_count: int = 0

    def capture(self) -> bytes:
        self.capture_count += 1
        return self.frame


@dataclass
class MockController:
    actions: list[tuple[str, float | None]] = field(default_factory=list)

    def press(self, button: str, duration_seconds: float = 0.1) -> None:
        if duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        self.actions.append((button, duration_seconds))

    def release_all(self) -> None:
        self.actions.append(("__RELEASE_ALL__", None))
