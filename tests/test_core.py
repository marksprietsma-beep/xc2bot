from __future__ import annotations

import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from xc2bot.config import Settings, load_settings
from xc2bot.diagnostics import DiagnosticStore, environment_info, redact
from xc2bot.interfaces import MockController, MockEmulator, MockScreenCapture
from xc2bot.safety import ActionTimeout, SafetyController, SafetyStop
from xc2bot.simulator import run_simulation
from xc2bot.state_machine import InvalidTransition, State, StateMachine


class ConfigTests(unittest.TestCase):
    def test_defaults_validate(self):
        self.assertEqual(Settings().max_retries, 2)

    def test_invalid_timeout_rejected(self):
        with self.assertRaises(ValueError):
            Settings(action_timeout_seconds=0)

    def test_unknown_config_key_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text('{"mystery": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)


class StateMachineTests(unittest.TestCase):
    def test_complete_encounter(self):
        machine = StateMachine()
        machine.transition(State.TARGET_DETECTED, "target")
        machine.transition(State.COMBAT, "engaged")
        machine.transition(State.VICTORY, "won")
        machine.transition(State.RESET, "reset")
        machine.transition(State.FIELD, "returned")
        self.assertEqual(machine.state, State.FIELD)

    def test_invalid_transition_rejected(self):
        with self.assertRaises(InvalidTransition):
            StateMachine().transition(State.VICTORY, "impossible")

    def test_unknown_observation_pauses_fail_closed(self):
        machine = StateMachine()
        machine.handle_unknown_observation("uncertain screen")
        self.assertEqual(machine.state, State.UNKNOWN)
        machine.handle_unknown_observation("still uncertain")
        self.assertEqual(machine.state, State.PAUSED)

    def test_resume_requires_confirmation(self):
        machine = StateMachine(State.PAUSED)
        with self.assertRaises(InvalidTransition):
            machine.transition(State.FIELD, "resume")
        machine.transition(State.FIELD, "operator approved", operator_confirmed=True)
        self.assertEqual(machine.state, State.FIELD)


class SafetyTests(unittest.TestCase):
    def test_emergency_stop(self):
        safety = SafetyController(Settings())
        safety.request_stop()
        with self.assertRaises(SafetyStop):
            safety.check()

    def test_retry_limit_bounded(self):
        safety = SafetyController(Settings(max_retries=1))
        safety.consume_retry()
        with self.assertRaises(SafetyStop):
            safety.consume_retry()

    def test_consecutive_errors_stop(self):
        safety = SafetyController(Settings(max_consecutive_errors=2))
        safety.record_error()
        safety.record_error()
        with self.assertRaises(SafetyStop):
            safety.check()

    def test_timeout_detected_after_action_returns(self):
        safety = SafetyController(Settings(action_timeout_seconds=0.001))
        with self.assertRaises(ActionTimeout):
            safety.run_action(lambda: time.sleep(0.01))

    def test_session_duration_limit(self):
        values = iter([0.0, 2.0])
        safety = SafetyController(Settings(max_session_seconds=1), clock=lambda: next(values))
        with self.assertRaises(SafetyStop):
            safety.check()


class MockInterfaceTests(unittest.TestCase):
    def test_mocks_are_deterministic(self):
        emulator = MockEmulator()
        capture = MockScreenCapture()
        controller = MockController()
        self.assertTrue(emulator.is_running())
        self.assertEqual(capture.capture(), b"mock-frame-not-a-real-screenshot")
        controller.press("A", 0.2)
        controller.release_all()
        self.assertEqual(controller.actions, [("A", 0.2), ("__RELEASE_ALL__", None)])


class DiagnosticsTests(unittest.TestCase):
    def test_persists_jsonl_and_sqlite(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(log_dir=temp)
            store = DiagnosticStore(temp, settings)
            store.log_event("test_event", state="FIELD", details={"note": "hello"})
            store.finish_session(final_state="FIELD")
            self.assertIn('"event_type": "test_event"', (Path(temp) / "events.jsonl").read_text(encoding="utf-8"))
            import sqlite3
            db = sqlite3.connect(Path(temp) / "sessions.sqlite3")
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            finally:
                db.close()

    def test_environment_info_omits_machine_paths(self):
        info = environment_info()
        self.assertIn("python_version", info)
        self.assertNotIn("home", info)
        self.assertNotIn("username", info)
        self.assertNotIn("cwd", info)

    def test_redacts_secret_and_windows_path(self):
        result = redact({"api_key": "abc123", "message": r"failed at C:\Users\Mark\private.txt"})
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertNotIn("C:\\Users", result["message"])

    def test_export_has_five_safe_files(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(log_dir=temp)
            store = DiagnosticStore(temp, settings)
            store.log_event("state_transition", state="VICTORY", details={"from": "COMBAT", "to": "VICTORY"})
            store.finish_session(final_state="VICTORY")
            output = Path(temp) / "export.zip"
            store.export_bundle(output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"summary.json", "events.jsonl", "config.json", "environment.json", "sessions.sqlite3"})
                summary = json.loads(archive.read("summary.json"))
                self.assertEqual(summary["victories_in_session"], 1)
                self.assertFalse(summary["screenshots_included"])

    def test_export_excludes_secret_and_absolute_path(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(log_dir=temp)
            store = DiagnosticStore(temp, settings)
            store.log_event("test_event", details={"api_token": "do-not-export", "error": r"C:\Users\Mark\x.txt"})
            output = Path(temp) / "export.zip"
            store.export_bundle(output)
            with zipfile.ZipFile(output) as archive:
                contents = b"".join(archive.read(name) for name in archive.namelist())
                self.assertNotIn(b"do-not-export", contents)
                self.assertNotIn(b"C:\\Users", contents)

    def test_simulation_finishes_in_field_with_ten_events(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_simulation(Settings(log_dir=temp))
            self.assertEqual(result["final_state"], "FIELD")
            lines = (Path(temp) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 10)
            self.assertEqual(result["captures"], 1)


if __name__ == "__main__":
    unittest.main()
