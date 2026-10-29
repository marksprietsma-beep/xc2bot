"""Small deterministic state machine. It does not interpret real game screens."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    FIELD = "FIELD"
    TARGET_DETECTED = "TARGET_DETECTED"
    COMBAT = "COMBAT"
    VICTORY = "VICTORY"
    DEFEAT = "DEFEAT"
    RESET = "RESET"
    PAUSED = "PAUSED"
    UNKNOWN = "UNKNOWN"


class InvalidTransition(ValueError):
    """Raised when a requested state change is not permitted."""


_ALLOWED: dict[State, set[State]] = {
    State.FIELD: {State.TARGET_DETECTED, State.PAUSED, State.UNKNOWN},
    State.TARGET_DETECTED: {State.COMBAT, State.FIELD, State.PAUSED, State.UNKNOWN},
    State.COMBAT: {State.VICTORY, State.DEFEAT, State.PAUSED, State.UNKNOWN},
    State.VICTORY: {State.RESET, State.FIELD, State.PAUSED, State.UNKNOWN},
    State.DEFEAT: {State.RESET, State.PAUSED, State.UNKNOWN},
    State.RESET: {State.FIELD, State.PAUSED, State.UNKNOWN},
    State.PAUSED: {State.FIELD},
    State.UNKNOWN: {State.PAUSED},
}


@dataclass(frozen=True, slots=True)
class Transition:
    previous: State
    current: State
    reason: str


class StateMachine:
    def __init__(self, initial: State = State.FIELD) -> None:
        self._state = initial

    @property
    def state(self) -> State:
        return self._state

    def transition(self, target: State, reason: str, *, operator_confirmed: bool = False) -> Transition:
        if target not in _ALLOWED[self._state]:
            raise InvalidTransition(f"Transition {self._state.value} -> {target.value} is not allowed")
        if self._state is State.PAUSED and target is State.FIELD and not operator_confirmed:
            raise InvalidTransition("Resuming from PAUSED requires explicit operator confirmation")
        previous = self._state
        self._state = target
        return Transition(previous, target, reason)

    def handle_unknown_observation(self, reason: str) -> Transition:
        """Fail closed: unknown observations enter UNKNOWN, then PAUSED."""
        if self._state is State.UNKNOWN:
            return self.transition(State.PAUSED, reason)
        if self._state is State.PAUSED:
            raise InvalidTransition("Machine is already paused")
        return self.transition(State.UNKNOWN, reason)
