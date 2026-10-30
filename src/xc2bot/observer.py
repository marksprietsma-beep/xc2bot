"""One-shot, read-only observation of the Xenoblade Chronicles 2 window."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .config import Settings
from .diagnostics import DiagnosticStore
from .window_capture import (
    WindowInfo,
    WindowNotForegroundError as CaptureWindowNotForegroundError,
    WindowsWindowProvider,
)


DEFAULT_GAME_TITLE = "Xenoblade Chronicles 2"


class ObservationError(RuntimeError):
    """Raised when a safe one-shot observation cannot be completed."""


class GameWindowNotFoundError(ObservationError):
    """Raised when no visible window matches the configured game title."""


class AmbiguousGameWindowError(ObservationError):
    """Raised when more than one matching window could be the target."""


class GameWindowNotForegroundError(ObservationError):
    """Raised when the target window is not active and cannot be activated."""


class WindowProvider(Protocol):
    def find_windows(self, title_contains: str) -> list[WindowInfo]: ...
    def activate_window(self, window: WindowInfo) -> WindowInfo: ...
    def capture_window(self, window: WindowInfo, output_path: str | Path) -> tuple[int, int]: ...


@dataclass(frozen=True, slots=True)
class ObservationResult:
    game_window_detected: bool
    window_title: str
    process_id: int
    window_handle: int
    width: int
    height: int
    screenshot_path: str
    diagnostic_session_id: str
    diagnostic_event: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def find_game_windows(
    title_contains: str = DEFAULT_GAME_TITLE,
    *,
    provider: WindowProvider | None = None,
) -> list[WindowInfo]:
    """Return visible matching windows without taking a screenshot."""
    active_provider = provider or WindowsWindowProvider()
    return active_provider.find_windows(title_contains)


def observe_once(
    settings: Settings,
    *,
    title_contains: str = DEFAULT_GAME_TITLE,
    output_path: str | Path | None = None,
    activate: bool = True,
    provider: WindowProvider | None = None,
) -> ObservationResult:
    """Capture one visible game-window frame and record a diagnostic event.

    The emulator is never sent game inputs by this command. The screenshot is
    stored separately from the diagnostic ZIP, which continues to exclude
    screenshots by default.
    """
    active_provider = provider or WindowsWindowProvider()
    candidates = active_provider.find_windows(title_contains)
    if not candidates:
        raise GameWindowNotFoundError(
            f"No visible window title contains {title_contains!r}. "
            "Start XC2 in Ryubing, wait until gameplay is visible, then retry. "
            "Use 'xc2bot windows' to inspect matching windows."
        )

    foreground = [candidate for candidate in candidates if candidate.is_foreground]
    if len(candidates) > 1 and len(foreground) != 1:
        choices = "; ".join(
            f"PID {item.process_id}: {item.title}" for item in candidates
        )
        raise AmbiguousGameWindowError(
            "More than one XC2 window was found and none is uniquely active. "
            f"Close the duplicate emulator or select one explicitly. Matches: {choices}"
        )
    selected = foreground[0] if foreground else candidates[0]

    if activate:
        try:
            selected = active_provider.activate_window(selected)
        except CaptureWindowNotForegroundError as exc:
            raise GameWindowNotForegroundError(str(exc)) from exc
    elif selected.is_minimized:
        raise ObservationError("The XC2 game window is minimized. Restore it and retry.")
    elif not selected.is_foreground:
        raise GameWindowNotForegroundError(
            "The XC2 game window is not active. Use the default command to activate it, "
            "or bring it to the foreground manually."
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    filename = f"xc2bot-{timestamp}-{uuid4().hex[:6]}.png"
    screenshot = Path(output_path) if output_path else Path(settings.log_dir) / "screenshots" / filename
    screenshot = screenshot.expanduser().resolve()

    width, height = active_provider.capture_window(selected, screenshot)

    diagnostics = DiagnosticStore(settings.log_dir, settings)
    diagnostics.log_event(
        "screenshot_captured",
        state="OBSERVING",
        action="capture_game_window",
        result="success",
        details={
            "filename": screenshot.name,
            "width": width,
            "height": height,
            "window_title": selected.title,
            "process_id": selected.process_id,
            "window_handle": selected.handle,
        },
    )
    diagnostics.finish_session(final_state="OBSERVED", status="completed")

    return ObservationResult(
        game_window_detected=True,
        window_title=selected.title,
        process_id=selected.process_id,
        window_handle=selected.handle,
        width=width,
        height=height,
        screenshot_path=str(screenshot),
        diagnostic_session_id=diagnostics.session_id,
        diagnostic_event="screenshot_captured",
    )
