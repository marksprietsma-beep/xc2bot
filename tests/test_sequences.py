from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from xc2bot.config import Settings
from xc2bot.keyboard import KeyboardMapping
from xc2bot.sequences import (
    ActionSequenceError,
    SequenceSafetyError,
    load_action_sequence,
    run_action_sequence,
)
from xc2bot.window_capture import WindowInfo


class FakeWindowProvider:
    def __init__(self, *, lose_focus_after_capture: int | None = None) -> None:
        self.window = WindowInfo(
            handle=101,
            title="Ryujinx 1.3.3",
            process_id=202,
            left=0,
            top=0,
            width=1280,
            height=720,
            is_foreground=True,
            is_minimized=False,
        )
        self.captures: list[Path] = []
        self.lose_focus_after_capture = lose_focus_after_capture

    def find_windows(self, title_contains: str) -> list[WindowInfo]:
        if title_contains.casefold() not in self.window.title.casefold():
            return []
        return [self.window]

    def activate_window(self, window: WindowInfo) -> WindowInfo:
        self.window = replace(self.window, is_foreground=True, is_minimized=False)
        return self.window

    def capture_window(self, window: WindowInfo, output_path: str | Path) -> tuple[int, int]:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-png")
        self.captures.append(output)
        if self.lose_focus_after_capture == len(self.captures):
            self.window = replace(self.window, is_foreground=False)
        return 1280, 720


class FakeController:
    def __init__(self) -> None:
        self.actions: list[tuple[str, float]] = []
        self.release_calls = 0

    def press(self, key: str, duration_seconds: float, *, focus_check) -> None:
        if not focus_check():
            raise RuntimeError("focus check failed")
        self.actions.append((key, duration_seconds))

    def release_all(self) -> None:
        self.release_calls += 1


class FakeEmergencyStop:
    def __init__(self, values: list[bool] | None = None) -> None:
        self.values = list(values or [])

    def is_pressed(self) -> bool:
        return self.values.pop(0) if self.values else False


class ActionSequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = KeyboardMapping(
            window_title_contains="Ryujinx 1.3.3",
            mappings={"MOVE_FORWARD": "W", "MOVE_RIGHT": "D", "MOVE_LEFT": "A"},
        )

    def _write_mapping(self, directory: str) -> Path:
        path = Path(directory) / "keyboard.json"
        path.write_text(json.dumps({
            "window_title_contains": self.mapping.window_title_contains,
            "mappings": self.mapping.mappings,
        }), encoding="utf-8")
        return path

    def _write_plan(self, directory: str, steps: list[dict]) -> Path:
        path = Path(directory) / "sequence.json"
        path.write_text(json.dumps({
            "description": "Short movement test",
            "steps": steps,
        }), encoding="utf-8")
        return path

    def test_loads_valid_plan_and_calculates_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write_plan(temp, [
                {"action": "MOVE_FORWARD", "duration_seconds": 0.25, "delay_after_seconds": 0.1},
                {"action": "MOVE_RIGHT", "duration_seconds": 0.2},
            ])
            plan = load_action_sequence(path, self.mapping)
            self.assertEqual(len(plan.steps), 2)
            self.assertAlmostEqual(plan.total_hold_seconds, 0.45)
            self.assertAlmostEqual(plan.total_delay_seconds, 0.1)
            self.assertAlmostEqual(plan.planned_seconds, 0.55)

    def test_rejects_unknown_action_before_any_window_or_input(self):
        with tempfile.TemporaryDirectory() as temp:
            mapping_path = self._write_mapping(temp)
            plan_path = self._write_plan(temp, [
                {"action": "ATTACK_STORY_BOSS", "duration_seconds": 0.25},
            ])
            provider = FakeWindowProvider()
            controller = FakeController()
            with self.assertRaises(ActionSequenceError):
                run_action_sequence(
                    Settings(log_dir=temp),
                    mapping_path=mapping_path,
                    plan_path=plan_path,
                    confirmed=True,
                    provider=provider,
                    controller=controller,
                )
            self.assertEqual(provider.captures, [])
            self.assertEqual(controller.actions, [])

    def test_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            mapping_path = self._write_mapping(temp)
            plan_path = self._write_plan(temp, [
                {"action": "MOVE_FORWARD", "duration_seconds": 0.25},
            ])
            provider = FakeWindowProvider()
            controller = FakeController()
            with self.assertRaises(ActionSequenceError):
                run_action_sequence(
                    Settings(log_dir=temp),
                    mapping_path=mapping_path,
                    plan_path=plan_path,
                    confirmed=False,
                    provider=provider,
                    controller=controller,
                )
            self.assertEqual(provider.captures, [])
            self.assertEqual(controller.actions, [])

    def test_rejects_too_many_steps_and_excessive_total_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            too_many = self._write_plan(temp, [
                {"action": "MOVE_FORWARD", "duration_seconds": 0.1}
                for _ in range(9)
            ])
            with self.assertRaises(ActionSequenceError):
                load_action_sequence(too_many, self.mapping)

            too_much_hold = self._write_plan(temp, [
                {"action": "MOVE_FORWARD", "duration_seconds": 1.0}
                for _ in range(6)
            ])
            with self.assertRaises(ActionSequenceError):
                load_action_sequence(too_much_hold, self.mapping)

    def test_runs_sequence_with_screenshots_and_parent_child_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            mapping_path = self._write_mapping(temp)
            plan_path = self._write_plan(temp, [
                {"action": "MOVE_FORWARD", "duration_seconds": 0.25},
                {"action": "MOVE_RIGHT", "duration_seconds": 0.2},
            ])
            provider = FakeWindowProvider()
            controller = FakeController()
            result = run_action_sequence(
                Settings(log_dir=temp),
                mapping_path=mapping_path,
                plan_path=plan_path,
                confirmed=True,
                provider=provider,
                controller=controller,
                emergency_stop=FakeEmergencyStop(),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.steps_completed, 2)
            self.assertEqual(result.total_steps, 2)
            self.assertEqual(controller.actions, [("W", 0.25), ("D", 0.2)])
            self.assertEqual(len(provider.captures), 4)
            self.assertTrue(all(Path(item["after_screenshot"]).exists() for item in result.action_results))
            events = [
                json.loads(line)
                for line in (Path(temp) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            parent_events = [event for event in events if event["session_id"] == result.sequence_id]
            event_types = [event["event_type"] for event in parent_events]
            self.assertIn("sequence_started", event_types)
            self.assertEqual(event_types.count("sequence_step_completed"), 2)
            self.assertIn("sequence_completed", event_types)
            child_ids = [
                event["details"]["action_session_id"]
                for event in parent_events
                if event["event_type"] == "sequence_step_completed"
            ]
            self.assertEqual(len(set(child_ids)), 2)

    def test_focus_loss_aborts_before_second_step(self):
        with tempfile.TemporaryDirectory() as temp:
            mapping_path = self._write_mapping(temp)
            plan_path = self._write_plan(temp, [
                {"action": "MOVE_FORWARD", "duration_seconds": 0.25},
                {"action": "MOVE_RIGHT", "duration_seconds": 0.2},
            ])
            provider = FakeWindowProvider(lose_focus_after_capture=2)
            controller = FakeController()
            with self.assertRaises(SequenceSafetyError):
                run_action_sequence(
                    Settings(log_dir=temp),
                    mapping_path=mapping_path,
                    plan_path=plan_path,
                    confirmed=True,
                    provider=provider,
                    controller=controller,
                    emergency_stop=FakeEmergencyStop(),
                )
            self.assertEqual(controller.actions, [("W", 0.25)])
            self.assertEqual(len(provider.captures), 2)
            events = (Path(temp) / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("sequence_aborted", events)

    def test_emergency_stop_between_steps_aborts_remaining_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            mapping_path = self._write_mapping(temp)
            plan_path = self._write_plan(temp, [
                {"action": "MOVE_FORWARD", "duration_seconds": 0.25},
                {"action": "MOVE_RIGHT", "duration_seconds": 0.2},
            ])
            provider = FakeWindowProvider()
            controller = FakeController()
            class StopAfterFirstAction:
                def is_pressed(self):
                    return len(provider.captures) >= 2
            with self.assertRaises(SequenceSafetyError):
                run_action_sequence(
                    Settings(log_dir=temp),
                    mapping_path=mapping_path,
                    plan_path=plan_path,
                    confirmed=True,
                    provider=provider,
                    controller=controller,
                    emergency_stop=StopAfterFirstAction(),
                )
            self.assertEqual(controller.actions, [("W", 0.25)])
            self.assertEqual(len(provider.captures), 2)


if __name__ == "__main__":
    unittest.main()
