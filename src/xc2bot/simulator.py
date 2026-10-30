"""A deterministic mock encounter; this is not real XC2 automation."""

from __future__ import annotations

from pathlib import Path

from .config import Settings
from .diagnostics import DiagnosticStore
from .interfaces import MockController, MockEmulator, MockScreenCapture
from .safety import SafetyController
from .state_machine import State, StateMachine


def run_simulation(settings: Settings, *, log_dir: str | Path | None = None) -> dict[str, object]:
    root = Path(log_dir or settings.log_dir)
    store = DiagnosticStore(root, settings)
    machine = StateMachine()
    safety = SafetyController(settings)
    emulator = MockEmulator()
    capture = MockScreenCapture()
    controller = MockController()

    def event(event_type: str, *, action: str | None = None, result: str | None = None, details: dict | None = None) -> None:
        safety.check()
        store.log_event(event_type, state=machine.state.value, action=action, result=result, details=details)

    try:
        event("session_started", result="ok", details={"mode": "simulation", "real_game": False})
        if not emulator.is_running():
            raise RuntimeError("Mock emulator is not running")
        capture.capture()
        controller.press("INTERACT")
        event("target_observed", action="INTERACT", result="confirmed", details={"mock": True})
        transition = machine.transition(State.TARGET_DETECTED, "mock target observed")
        store.log_event("state_transition", state=machine.state.value, result="ok", details={"from": transition.previous.value, "to": transition.current.value, "reason": transition.reason})
        controller.press("ATTACK")
        transition = machine.transition(State.COMBAT, "mock encounter started")
        store.log_event("state_transition", state=machine.state.value, action="ATTACK", result="ok", details={"from": transition.previous.value, "to": transition.current.value, "reason": transition.reason})
        event("action", action="ATTACK", result="mock attack issued")
        transition = machine.transition(State.VICTORY, "mock victory observed")
        store.log_event("state_transition", state=machine.state.value, result="confirmed", details={"from": transition.previous.value, "to": transition.current.value, "reason": transition.reason})
        event("victory", result="confirmed", details={"mock": True})
        transition = machine.transition(State.RESET, "mock reset requested")
        store.log_event("state_transition", state=machine.state.value, result="ok", details={"from": transition.previous.value, "to": transition.current.value, "reason": transition.reason})
        transition = machine.transition(State.FIELD, "mock reset complete")
        store.log_event("state_transition", state=machine.state.value, result="ok", details={"from": transition.previous.value, "to": transition.current.value, "reason": transition.reason})
        event("session_completed", result="ok", details={"final_state": machine.state.value, "mock_controller_actions": len(controller.actions), "mock_capture_count": capture.capture_count})
        store.finish_session(final_state=machine.state.value)
    except Exception as exc:
        try:
            store.log_event("session_error", state=machine.state.value, result="failed", details={"error_type": type(exc).__name__, "error": str(exc)})
            store.finish_session(final_state=machine.state.value, status="failed")
        finally:
            controller.release_all()
        raise

    return {"session_id": store.session_id, "final_state": machine.state.value, "events_path": str(store.events_path), "database_path": str(store.database_path), "controller_actions": len(controller.actions), "captures": capture.capture_count}
