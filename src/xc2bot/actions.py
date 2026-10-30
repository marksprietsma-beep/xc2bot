"""Manually confirmed, one-shot keyboard actions with before/after screenshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .config import Settings
from .diagnostics import DiagnosticStore
from .keyboard import (
    EMERGENCY_STOP_LABEL,
    KeyboardMapping,
    WindowsKeyboardController,
    load_keyboard_mapping,
    resolve_action_key,
    validate_hold_duration,
)
from .window_capture import WindowInfo, WindowsWindowProvider


class KeyboardActionError(RuntimeError):
    """Raised when a mapped keyboard action cannot safely run."""


class TargetWindowNotFound(KeyboardActionError):
    """Raised when the requested emulator window cannot be found."""


class TargetWindowNotForeground(KeyboardActionError):
    """Raised when the requested emulator window is not the active window."""


class AmbiguousTargetWindow(KeyboardActionError):
    """Raised when more than one matching foreground window exists."""


class ActionWindowProvider(Protocol):
    def find_windows(self, title_contains: str) -> list[WindowInfo]: ...
    def capture_window(self, window: WindowInfo, output_path: str | Path) -> tuple[int, int]: ...


class KeyboardController(Protocol):
    def press(
        self,
        key: str,
        duration_seconds: float,
        *,
        focus_check,
    ) -> None: ...
    def release_all(self) -> None: ...


@dataclass(frozen=True, slots=True)
class KeyboardActionResult:
    action: str
    key: str
    duration_seconds: float
    window_title: str
    process_id: int
    before_screenshot: str
    after_screenshot: str | None
    diagnostic_session_id: str
    emergency_stop: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _foreground_target(
    provider: ActionWindowProvider,
    title_contains: str,
) -> WindowInfo:
    candidates = provider.find_windows(title_contains)
    if not candidates:
        raise TargetWindowNotFound(
            f"No visible window title contains {title_contains!r}. Start XC2 in Ryujinx and retry."
        )
    foreground = [
        item for item in candidates
        if item.is_foreground and not item.is_minimized
    ]
    if len(foreground) > 1:
        raise AmbiguousTargetWindow(
            "More than one matching emulator window is foreground. Close duplicates and retry."
        )
    if not foreground:
        raise TargetWindowNotForeground(
            "Ryujinx is not the active window. Click the XC2 game window, then retry. "
            "The input command will not steal focus or restore a minimised window."
        )
    return foreground[0]


def _screenshot_path(root: Path, phase: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return root / "screenshots" / f"xc2bot-input-{stamp}-{phase}-{uuid4().hex[:6]}.png"


def perform_mapped_action(
    settings: Settings,
    *,
    mapping_path: str | Path,
    action: str,
    duration_seconds: float,
    confirmed: bool = False,
    window_title: str | None = None,
    provider: ActionWindowProvider | None = None,
    controller: KeyboardController | None = None,
) -> KeyboardActionResult:
    """Send one mapped key only while the selected Ryujinx window is foreground.

    This command never activates the emulator, never repeats actions, and caps
    every hold at one second. A global Ctrl+Alt+F12 emergency chord is polled
    while the key is held. Screenshots are local and excluded from ZIP exports.
    """
    if not confirmed:
        raise KeyboardActionError("A manual keyboard action requires explicit operator confirmation.")
    mapping: KeyboardMapping = load_keyboard_mapping(mapping_path)
    key = resolve_action_key(mapping, action)
    duration = validate_hold_duration(duration_seconds)
    title = (window_title or mapping.window_title_contains).strip()
    if not title:
        raise ValueError("window title match must not be empty")

    active_provider = provider or WindowsWindowProvider()
    target = _foreground_target(active_provider, title)
    active_controller = controller or WindowsKeyboardController()
    store = DiagnosticStore(settings.log_dir, settings)
    before_path = _screenshot_path(Path(settings.log_dir), "before")
    after_path: Path | None = None
    action_started = False

    def focus_check() -> bool:
        matches = [
            item for item in active_provider.find_windows(title)
            if item.handle == target.handle
        ]
        return (
            len(matches) == 1
            and matches[0].is_foreground
            and not matches[0].is_minimized
        )

    try:
        store.log_event(
            "session_started",
            state="INPUT_READY",
            result="ok",
            details={
                "mode": "manual_single_keyboard_action",
                "window_title": target.title,
                "process_id": target.process_id,
                "story_progression": False,
            },
        )

        before_width, before_height = active_provider.capture_window(target, before_path)
        store.log_event(
            "screenshot_captured",
            state="INPUT_READY",
            action="capture_before",
            result="success",
            details={
                "phase": "before",
                "filename": before_path.name,
                "width": before_width,
                "height": before_height,
                "window_title": target.title,
            },
        )

        if not focus_check():
            raise TargetWindowNotForeground("Ryujinx lost foreground focus before input; no key was sent.")

        action_started = True
        store.log_event(
            "keyboard_action_started",
            state="INPUT_ACTIVE",
            action=action.strip().upper(),
            result="sent",
            details={
                "key": key,
                "duration_seconds": duration,
                "window_title": target.title,
                "process_id": target.process_id,
                "emergency_stop": EMERGENCY_STOP_LABEL,
                "confirmation": "explicit_cli_flag",
            },
        )
        active_controller.press(key, duration, focus_check=focus_check)
        store.log_event(
            "keyboard_action_completed",
            state="INPUT_COMPLETE",
            action=action.strip().upper(),
            result="key_released",
            details={
                "key": key,
                "duration_seconds": duration,
                "key_released": True,
                "window_title": target.title,
            },
        )

        after_target = None
        try:
            after_target = _foreground_target(active_provider, title)
            if after_target.handle != target.handle:
                after_target = None
        except KeyboardActionError:
            after_target = None

        if after_target is not None:
            after_path = _screenshot_path(Path(settings.log_dir), "after")
            try:
                after_width, after_height = active_provider.capture_window(after_target, after_path)
                store.log_event(
                    "screenshot_captured",
                    state="INPUT_COMPLETE",
                    action="capture_after",
                    result="success",
                    details={
                        "phase": "after",
                        "filename": after_path.name,
                        "width": after_width,
                        "height": after_height,
                        "window_title": after_target.title,
                    },
                )
            except Exception as exc:
                after_path = None
                store.log_event(
                    "screenshot_capture_failed",
                    state="INPUT_COMPLETE",
                    action="capture_after",
                    result="partial",
                    details={"phase": "after", "error_type": type(exc).__name__, "error": str(exc)},
                )
        else:
            store.log_event(
                "screenshot_skipped",
                state="INPUT_COMPLETE",
                action="capture_after",
                result="skipped",
                details={"phase": "after", "reason": "target_window_not_foreground"},
            )

        store.finish_session(
            final_state="INPUT_COMPLETE",
            status="completed" if after_path is not None else "partial",
        )
        return KeyboardActionResult(
            action=action.strip().upper(),
            key=key,
            duration_seconds=duration,
            window_title=target.title,
            process_id=target.process_id,
            before_screenshot=str(before_path),
            after_screenshot=str(after_path) if after_path is not None else None,
            diagnostic_session_id=store.session_id,
            emergency_stop=EMERGENCY_STOP_LABEL,
        )
    except Exception as exc:
        try:
            active_controller.release_all()
        except Exception as release_exc:
            store.log_event(
                "keyboard_release_failed",
                state="INPUT_ERROR",
                action=action.strip().upper(),
                result="failed",
                details={"error_type": type(release_exc).__name__, "error": str(release_exc)},
            )
        store.log_event(
            "keyboard_action_failed" if action_started else "keyboard_action_rejected",
            state="INPUT_ERROR",
            action=action.strip().upper(),
            result="failed",
            details={
                "key": key,
                "duration_seconds": duration,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "key_release_attempted": True,
            },
        )
        store.finish_session(final_state="INPUT_ERROR", status="failed")
        raise
    finally:
        active_controller.release_all()
