"""Validated, manually confirmed sequences of bounded single-key actions."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from .actions import KeyboardActionResult, perform_mapped_action
from .config import Settings
from .diagnostics import DiagnosticStore
from .keyboard import (
    EmergencyStopProbe,
    KeyboardMapping,
    WindowsEmergencyStopProbe,
    load_keyboard_mapping,
    resolve_action_key,
    validate_hold_duration,
)
from .window_capture import WindowInfo, WindowsWindowProvider


MAX_SEQUENCE_STEPS = 8
MAX_SEQUENCE_TOTAL_HOLD_SECONDS = 5.0
MAX_SEQUENCE_STEP_DELAY_SECONDS = 2.0
MAX_SEQUENCE_TOTAL_DELAY_SECONDS = 10.0
MAX_SEQUENCE_PLANNED_SECONDS = 15.0
MAX_SEQUENCE_RUNTIME_SECONDS = 45.0
SAFETY_POLL_SECONDS = 0.05


class ActionSequenceError(RuntimeError):
    """Raised when a sequence is invalid or cannot continue safely."""


class SequenceSafetyError(ActionSequenceError):
    """Raised when focus, emergency-stop or runtime checks abort a sequence."""


class SequenceWindowProvider(Protocol):
    def find_windows(self, title_contains: str) -> list[WindowInfo]: ...


@dataclass(frozen=True, slots=True)
class SequenceStep:
    action: str
    duration_seconds: float
    delay_after_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class ActionSequencePlan:
    description: str
    steps: tuple[SequenceStep, ...]

    @property
    def total_hold_seconds(self) -> float:
        return sum(step.duration_seconds for step in self.steps)

    @property
    def total_delay_seconds(self) -> float:
        return sum(step.delay_after_seconds for step in self.steps)

    @property
    def planned_seconds(self) -> float:
        return self.total_hold_seconds + self.total_delay_seconds


@dataclass(frozen=True, slots=True)
class ActionSequenceResult:
    sequence_id: str
    description: str
    status: str
    steps_completed: int
    total_steps: int
    elapsed_seconds: float
    action_results: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def load_action_sequence(
    path: str | Path,
    mapping: KeyboardMapping,
) -> ActionSequencePlan:
    """Load and fully validate a sequence before any game input is attempted."""
    plan_path = Path(path)
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ActionSequenceError(f"Cannot read sequence plan: {plan_path}") from exc
    except json.JSONDecodeError as exc:
        raise ActionSequenceError(f"Sequence plan is not valid JSON: {plan_path}") from exc

    if not isinstance(raw, dict):
        raise ActionSequenceError("Sequence plan must be a JSON object")
    unknown = set(raw) - {"description", "steps"}
    if unknown:
        raise ActionSequenceError(f"Unknown sequence fields: {', '.join(sorted(unknown))}")

    description = raw.get("description", "Manual action sequence")
    if not isinstance(description, str) or not description.strip() or len(description.strip()) > 120:
        raise ActionSequenceError("description must be a non-empty string of at most 120 characters")

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ActionSequenceError("steps must be a non-empty array")
    if len(raw_steps) > MAX_SEQUENCE_STEPS:
        raise ActionSequenceError(f"A sequence may contain at most {MAX_SEQUENCE_STEPS} steps")

    steps: list[SequenceStep] = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            raise ActionSequenceError(f"Step {index} must be an object")
        unknown_step_fields = set(raw_step) - {
            "action",
            "duration_seconds",
            "delay_after_seconds",
        }
        if unknown_step_fields:
            raise ActionSequenceError(
                f"Unknown fields in step {index}: {', '.join(sorted(unknown_step_fields))}"
            )

        action = raw_step.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ActionSequenceError(f"Step {index} requires a non-empty action name")
        action = action.strip().upper()
        try:
            resolve_action_key(mapping, action)
        except ValueError as exc:
            raise ActionSequenceError(f"Step {index}: {exc}") from exc

        duration_value = raw_step.get("duration_seconds")
        if isinstance(duration_value, bool) or not isinstance(duration_value, (int, float)):
            raise ActionSequenceError(f"Step {index} duration_seconds must be a number")
        try:
            duration = validate_hold_duration(duration_value)
        except ValueError as exc:
            raise ActionSequenceError(f"Step {index}: {exc}") from exc

        delay_value = raw_step.get("delay_after_seconds", 0.0)
        if isinstance(delay_value, bool) or not isinstance(delay_value, (int, float)):
            raise ActionSequenceError(f"Step {index} delay_after_seconds must be a number")
        delay = float(delay_value)
        if not math.isfinite(delay) or delay < 0 or delay > MAX_SEQUENCE_STEP_DELAY_SECONDS:
            raise ActionSequenceError(
                f"Step {index} delay_after_seconds must be between 0 and "
                f"{MAX_SEQUENCE_STEP_DELAY_SECONDS:.1f} seconds"
            )
        if index == len(raw_steps) and delay != 0:
            raise ActionSequenceError("The final step must have delay_after_seconds set to 0")

        steps.append(SequenceStep(action, duration, delay))

    plan = ActionSequencePlan(description.strip(), tuple(steps))
    if plan.total_hold_seconds > MAX_SEQUENCE_TOTAL_HOLD_SECONDS:
        raise ActionSequenceError(
            f"Total key-hold time cannot exceed {MAX_SEQUENCE_TOTAL_HOLD_SECONDS:.1f} seconds"
        )
    if plan.total_delay_seconds > MAX_SEQUENCE_TOTAL_DELAY_SECONDS:
        raise ActionSequenceError(
            f"Total inter-step delay cannot exceed {MAX_SEQUENCE_TOTAL_DELAY_SECONDS:.1f} seconds"
        )
    if plan.planned_seconds > MAX_SEQUENCE_PLANNED_SECONDS:
        raise ActionSequenceError(
            f"Total planned sequence time cannot exceed {MAX_SEQUENCE_PLANNED_SECONDS:.1f} seconds"
        )
    return plan


def _assert_sequence_focus(
    provider: SequenceWindowProvider,
    title: str,
    process_id: int,
) -> None:
    matches = [
        window
        for window in provider.find_windows(title)
        if window.process_id == process_id
    ]
    if len(matches) != 1 or not matches[0].is_foreground or matches[0].is_minimized:
        raise SequenceSafetyError(
            "Ryujinx lost foreground focus during the sequence. Remaining actions were cancelled."
        )


def _wait_between_actions(
    delay_seconds: float,
    *,
    provider: SequenceWindowProvider,
    title: str,
    process_id: int,
    emergency_stop: EmergencyStopProbe,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
    sequence_started: float,
) -> None:
    deadline = clock() + delay_seconds
    while True:
        if emergency_stop.is_pressed():
            raise SequenceSafetyError("Emergency stop pressed between actions; sequence cancelled.")
        if clock() - sequence_started > MAX_SEQUENCE_RUNTIME_SECONDS:
            raise SequenceSafetyError(
                f"Sequence exceeded its {MAX_SEQUENCE_RUNTIME_SECONDS:.0f}-second runtime limit."
            )
        _assert_sequence_focus(provider, title, process_id)
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleeper(min(SAFETY_POLL_SECONDS, remaining))


def run_action_sequence(
    settings: Settings,
    *,
    mapping_path: str | Path,
    plan_path: str | Path,
    confirmed: bool = False,
    window_title: str | None = None,
    provider: SequenceWindowProvider | None = None,
    controller=None,
    emergency_stop: EmergencyStopProbe | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ActionSequenceResult:
    """Run a validated, short sequence; abort on focus loss or emergency stop.

    Each step is still a separate one-key action with its own before/after
    screenshots and child diagnostic session. A parent sequence session links
    the child sessions. No key combinations, loops, retries or story actions
    are supported.
    """
    if not confirmed:
        raise ActionSequenceError("A sequence requires the explicit --confirm acknowledgement.")

    mapping = load_keyboard_mapping(mapping_path)
    plan = load_action_sequence(plan_path, mapping)
    title = (window_title or mapping.window_title_contains).strip()
    if not title:
        raise ActionSequenceError("window title match must not be empty")

    active_provider = provider or WindowsWindowProvider()
    stop_probe = emergency_stop or WindowsEmergencyStopProbe()
    sequence_id = f"sequence-{uuid4().hex[:12]}"
    store = DiagnosticStore(settings.log_dir, settings, session_id=sequence_id)
    sequence_started = clock()
    completed_results: list[KeyboardActionResult] = []
    previous_process_id: int | None = None
    failed_step_index: int | None = None

    store.log_event(
        "sequence_started",
        state="SEQUENCE_RUNNING",
        result="started",
        details={
            "sequence_id": sequence_id,
            "description": plan.description,
            "step_count": len(plan.steps),
            "total_hold_seconds": plan.total_hold_seconds,
            "total_delay_seconds": plan.total_delay_seconds,
            "planned_seconds": plan.planned_seconds,
            "maximum_runtime_seconds": MAX_SEQUENCE_RUNTIME_SECONDS,
            "story_progression": False,
        },
    )

    try:
        for index, step in enumerate(plan.steps, start=1):
            failed_step_index = index
            if clock() - sequence_started > MAX_SEQUENCE_RUNTIME_SECONDS:
                raise SequenceSafetyError(
                    f"Sequence exceeded its {MAX_SEQUENCE_RUNTIME_SECONDS:.0f}-second runtime limit."
                )

            if previous_process_id is not None:
                if stop_probe.is_pressed():
                    raise SequenceSafetyError("Emergency stop pressed between actions; sequence cancelled.")
                _assert_sequence_focus(active_provider, title, previous_process_id)

            store.log_event(
                "sequence_step_started",
                state="SEQUENCE_RUNNING",
                action=step.action,
                result="started",
                details={
                    "sequence_id": sequence_id,
                    "step_index": index,
                    "step_count": len(plan.steps),
                    "duration_seconds": step.duration_seconds,
                    "delay_after_seconds": step.delay_after_seconds,
                },
            )

            result = perform_mapped_action(
                settings,
                mapping_path=mapping_path,
                action=step.action,
                duration_seconds=step.duration_seconds,
                confirmed=True,
                window_title=title,
                provider=active_provider,  # type: ignore[arg-type]
                controller=controller,
            )
            if previous_process_id is not None and result.process_id != previous_process_id:
                raise SequenceSafetyError("The target emulator process changed during the sequence.")
            previous_process_id = result.process_id
            completed_results.append(result)

            store.log_event(
                "sequence_step_completed",
                state="SEQUENCE_RUNNING",
                action=step.action,
                result="completed",
                elapsed_seconds=clock() - sequence_started,
                details={
                    "sequence_id": sequence_id,
                    "step_index": index,
                    "step_count": len(plan.steps),
                    "action_session_id": result.diagnostic_session_id,
                    "key": result.key,
                    "before_screenshot": Path(result.before_screenshot).name,
                    "after_screenshot": (
                        Path(result.after_screenshot).name if result.after_screenshot else None
                    ),
                },
            )

            if step.delay_after_seconds > 0 and index < len(plan.steps):
                _wait_between_actions(
                    step.delay_after_seconds,
                    provider=active_provider,
                    title=title,
                    process_id=previous_process_id,
                    emergency_stop=stop_probe,
                    clock=clock,
                    sleeper=sleeper,
                    sequence_started=sequence_started,
                )

        elapsed = clock() - sequence_started
        status = (
            "completed"
            if all(result.after_screenshot is not None for result in completed_results)
            else "partial"
        )
        store.log_event(
            "sequence_completed",
            state="SEQUENCE_COMPLETE",
            result=status,
            elapsed_seconds=elapsed,
            details={
                "sequence_id": sequence_id,
                "steps_completed": len(completed_results),
                "step_count": len(plan.steps),
                "action_session_ids": [result.diagnostic_session_id for result in completed_results],
            },
        )
        store.finish_session(
            final_state="SEQUENCE_COMPLETE",
            status=status,
        )
        return ActionSequenceResult(
            sequence_id=sequence_id,
            description=plan.description,
            status=status,
            steps_completed=len(completed_results),
            total_steps=len(plan.steps),
            elapsed_seconds=elapsed,
            action_results=tuple(result.as_dict() for result in completed_results),
        )
    except Exception as exc:
        store.log_event(
            "sequence_aborted",
            state="SEQUENCE_ABORTED",
            action=None,
            result="failed",
            elapsed_seconds=clock() - sequence_started,
            details={
                "sequence_id": sequence_id,
                "failed_step_index": failed_step_index,
                "steps_completed": len(completed_results),
                "step_count": len(plan.steps),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "remaining_steps_cancelled": True,
            },
        )
        store.finish_session(final_state="SEQUENCE_ABORTED", status="failed")
        raise
