"""Manually confirmed, one-shot keyboard actions with before/after screenshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from .config import Settings
from .diagnostics import DiagnosticStore, utc_now
from .keyboard import (
    EMERGENCY_STOP_LABEL,
    KeyboardMapping,
    WindowsKeyboardController,
    load_keyboard_mapping,
    resolve_action_key,
    validate_hold_duration,
)
from .window_capture import (
    ForegroundWindowInfo, WindowInfo, WindowNotForegroundError, WindowsWindowProvider,
)


class KeyboardActionError(RuntimeError):
    """Raised when a mapped keyboard action cannot safely run."""


class TargetWindowNotFound(KeyboardActionError):
    """Raised when the requested emulator window cannot be found."""


class TargetWindowNotForeground(KeyboardActionError):
    """Raised when the requested emulator window is not the active window."""


class AmbiguousTargetWindow(KeyboardActionError):
    """Raised when more than one matching foreground window exists."""


class ActionWindowProvider(Protocol):
    def get_foreground_window(self) -> ForegroundWindowInfo: ...
    def find_windows(self, title_contains: str) -> list[WindowInfo]: ...
    def activate_window(self, window: WindowInfo) -> WindowInfo: ...
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


def _select_target_window(
    provider: ActionWindowProvider,
    title_contains: str,
) -> WindowInfo:
    candidates = provider.find_windows(title_contains)
    if not candidates:
        raise TargetWindowNotFound(
            f"No visible window title contains {title_contains!r}. Start XC2 in Ryujinx and retry."
        )
    if len(candidates) == 1:
        return candidates[0]
    foreground = [item for item in candidates if item.is_foreground and not item.is_minimized]
    if len(foreground) == 1:
        return foreground[0]
    choices = "; ".join(f"PID {item.process_id}: {item.title}" for item in candidates)
    raise AmbiguousTargetWindow(
        "More than one Ryujinx window matches the configured title and none is uniquely active. "
        f"Use a more specific window_title_contains value. Matches: {choices}"
    )


def _activate_target_window(
    provider: ActionWindowProvider,
    selected: WindowInfo,
) -> WindowInfo:
    if selected.is_foreground and not selected.is_minimized:
        return selected
    try:
        active = provider.activate_window(selected)
    except WindowNotForegroundError as exc:
        raise TargetWindowNotForeground(str(exc)) from exc
    if active.is_minimized or not active.is_foreground:
        raise TargetWindowNotForeground(
            "Windows did not make the Ryujinx game window active. No keyboard input was sent."
        )
    return active


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
    mapping: KeyboardMapping | None = None,
    expected_target: WindowInfo | None = None,
    activate: bool = True,
    safety_check: Callable[[], None] | None = None,
    diagnostic_session_id: str | None = None,
    parent_sequence_id: str | None = None,
) -> KeyboardActionResult:
    """Activate Ryujinx, then send one mapped key with strict safety checks.

    The command remembers that PowerShell launched it, activates the uniquely
    selected emulator window, and verifies focus before and during the key hold.
    It never repeats actions and caps every hold at one second. A global
    Ctrl+Alt+F12 emergency chord is polled while the key is held. Screenshots
    are local and excluded from ZIP exports.
    """
    if not confirmed:
        raise KeyboardActionError("A manual keyboard action requires explicit operator confirmation.")
    mapping = mapping if mapping is not None else load_keyboard_mapping(mapping_path)
    key = resolve_action_key(mapping, action)
    duration = validate_hold_duration(duration_seconds)
    title = (mapping.window_title_contains if window_title is None else window_title).strip()
    if not title:
        raise ValueError("window title match must not be empty")

    active_provider = provider or WindowsWindowProvider()
    active_controller = controller or WindowsKeyboardController()
    store = DiagnosticStore(settings.log_dir, settings, session_id=diagnostic_session_id)
    before_path = _screenshot_path(Path(settings.log_dir), "before")
    after_path: Path | None = None
    action_started = False
    target: WindowInfo | None = None

    def check_safety() -> None:
        if safety_check is not None:
            safety_check()

    def focus_check() -> bool:
        check_safety()
        matches = [
            item for item in active_provider.find_windows(title)
            if item.handle == target.handle and item.process_id == target.process_id
        ]
        return (
            len(matches) == 1
            and matches[0].is_foreground
            and not matches[0].is_minimized
        )

    def log_foreground(checkpoint: str, *, capture_phase: str | None = None) -> None:
        # Identity telemetry only: never activate/restore, print to the console,
        # or use its result to override the existing foreground safety checks.
        assert target is not None  # Checkpoints begin only after target selection.
        details: dict[str, object] = {
            "checkpoint": checkpoint,
            "capture_phase": capture_phase,
            "observed_at": utc_now(),
            "parent_sequence_id": parent_sequence_id,
            "target": {"handle": target.handle, "process_id": target.process_id,
                       "title": target.title},
        }
        try:
            foreground = active_provider.get_foreground_window()
            details["foreground"] = asdict(foreground)
            observed = "observed"
        except Exception as exc:
            # An unavailable observation is not proof of target focus. The
            # original fail-closed checks still run; persistence failures below
            # retain the existing stop-on-lost-diagnostics behaviour.
            details["foreground"] = None
            details["observation_error_type"] = type(exc).__name__
            observed = "unavailable"
        if checkpoint == "action_failed":
            state = "INPUT_ERROR"
        elif checkpoint == "after_action" or capture_phase == "after":
            state = "INPUT_COMPLETE"
        else:
            state = "INPUT_READY"
        store.log_event(
            "foreground_checkpoint",
            state=state,
            action=action.strip().upper(), result=observed, details=details,
        )

    try:
        store.log_event(
            "action_session_linked", state="INPUT_READY", result="started",
            details={"parent_sequence_id": parent_sequence_id},
        )
        check_safety()
        if expected_target is None:
            selected_target = _select_target_window(active_provider, title)
        else:
            matches = [item for item in active_provider.find_windows(title)
                       if item.handle == expected_target.handle
                       and item.process_id == expected_target.process_id]
            if len(matches) != 1:
                raise TargetWindowNotFound("The pinned emulator window disappeared; no input was sent.")
            selected_target = matches[0]
        if activate:
            target = _activate_target_window(active_provider, selected_target)
        else:
            target = selected_target
        if (target.handle != selected_target.handle or target.process_id != selected_target.process_id
                or not focus_check()):
            raise TargetWindowNotForeground("The pinned Ryujinx window is not foreground; no input was sent.")
        store.log_event(
            "session_started",
            state="INPUT_READY",
            result="ok",
            details={
                "mode": "manual_single_keyboard_action",
                "window_title": target.title,
                "process_id": target.process_id,
                "story_progression": False,
                "window_activation": "automatic" if activate else "foreground_only",
                "parent_sequence_id": parent_sequence_id,
            },
        )

        log_foreground("before_screenshot", capture_phase="before")
        before_width, before_height = active_provider.capture_window(target, before_path)
        log_foreground("after_screenshot", capture_phase="before")
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

        log_foreground("before_keyboard_input")
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
        active_controller.release_all()
        log_foreground("after_action")
        check_safety()
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

        after_matches = [
            item for item in active_provider.find_windows(title)
            if item.handle == target.handle and item.process_id == target.process_id
            and item.is_foreground and not item.is_minimized
        ]
        after_target = after_matches[0] if len(after_matches) == 1 else None

        if after_target is not None:
            after_path = _screenshot_path(Path(settings.log_dir), "after")
            try:
                log_foreground("before_screenshot", capture_phase="after")
                after_width, after_height = active_provider.capture_window(after_target, after_path)
                log_foreground("after_screenshot", capture_phase="after")
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

        check_safety()
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
    except (Exception, KeyboardInterrupt) as exc:
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
        if target is not None:
            log_foreground("action_failed")
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
                "input_may_have_occurred": action_started,
            },
        )
        store.finish_session(
            final_state="INPUT_ERROR",
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
        )
        raise
    finally:
        active_controller.release_all()
