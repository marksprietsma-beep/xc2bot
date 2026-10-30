"""Fail-closed session guards. Synchronous timeouts are detected after return, not pre-empted."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

from .config import Settings

T = TypeVar("T")


class SafetyStop(RuntimeError):
    """Raised when the session must stop or pause safely."""


class ActionTimeout(SafetyStop):
    """Raised when an action returns after its configured timeout."""


class SafetyController:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self._clock = clock
        self._started = clock()
        self._stop_event = threading.Event()
        self._consecutive_errors = 0
        self._retries_used = 0

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self, reason: str = "Emergency stop requested") -> None:
        self._stop_event.set()

    def check(self) -> None:
        if self._stop_event.is_set():
            raise SafetyStop("Emergency stop requested")
        if self._clock() - self._started >= self.settings.max_session_seconds:
            self._stop_event.set()
            raise SafetyStop("Maximum session duration exceeded")
        if self._consecutive_errors >= self.settings.max_consecutive_errors:
            self._stop_event.set()
            raise SafetyStop("Consecutive error limit exceeded")

    def record_success(self) -> None:
        self._consecutive_errors = 0

    def record_error(self) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.settings.max_consecutive_errors:
            self._stop_event.set()

    def consume_retry(self) -> None:
        self.check()
        if self._retries_used >= self.settings.max_retries:
            self._stop_event.set()
            raise SafetyStop("Retry limit exhausted")
        self._retries_used += 1

    def run_action(self, action: Callable[[], T], *, timeout_seconds: float | None = None) -> T:
        """Run and time an action. Cannot interrupt a blocked synchronous function."""
        self.check()
        timeout = timeout_seconds if timeout_seconds is not None else self.settings.action_timeout_seconds
        started = self._clock()
        result = action()
        elapsed = self._clock() - started
        if elapsed > timeout:
            self._stop_event.set()
            raise ActionTimeout(f"Action exceeded timeout ({elapsed:.3f}s > {timeout:.3f}s)")
        self.record_success()
        return result
