from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import os
import ctypes

from xc2bot.actions import (
    TargetWindowNotForeground,
    perform_mapped_action,
)
from xc2bot.config import Settings
from xc2bot.keyboard import (
    EmergencyStopRequested,
    KeyboardControlError,
    WindowsKeyboardController,
    WindowsSendInputSender,
    _INPUT,
    load_keyboard_mapping,
    resolve_action_key,
    validate_hold_duration,
)
from xc2bot.window_capture import WindowInfo


class FakeSender:
    def __init__(self):
        self.events: list[tuple[str, bool]] = []

    def send_key(self, key: str, *, key_down: bool) -> None:
        self.events.append((key, key_down))


class FakeStopProbe:
    def __init__(self, values: list[bool] | None = None):
        self.values = list(values or [False])
        self.calls = 0

    def is_pressed(self) -> bool:
        self.calls += 1
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


class FakeWindowProvider:
    def __init__(self, *, foreground: bool = True):
        self.window = WindowInfo(
            handle=123,
            title="Ryujinx 1.3.3",
            process_id=456,
            left=10,
            top=20,
            width=1280,
            height=760,
            is_foreground=foreground,
            is_minimized=False,
        )
        self.captures: list[Path] = []

    def find_windows(self, title_contains: str) -> list[WindowInfo]:
        if title_contains.casefold() in self.window.title.casefold():
            return [self.window]
        return []

    def capture_window(self, window: WindowInfo, output_path: str | Path) -> tuple[int, int]:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x89PNG\r\n\x1a\nFAKE-PNG")
        self.captures.append(output)
        return window.width, window.height


class FakeController:
    def __init__(self):
        self.actions: list[tuple[str, float]] = []
        self.release_calls = 0

    def press(self, key: str, duration_seconds: float, *, focus_check) -> None:
        if not focus_check():
            raise RuntimeError("focus lost")
        self.actions.append((key, duration_seconds))

    def release_all(self) -> None:
        self.release_calls += 1


class KeyboardMappingTests(unittest.TestCase):
    def test_loads_and_resolves_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "keyboard.json"
            path.write_text(json.dumps({
                "window_title_contains": "Ryujinx 1.3.3",
                "mappings": {"move_forward": "w", "confirm": "ENTER"},
            }), encoding="utf-8")
            mapping = load_keyboard_mapping(path)
            self.assertEqual(mapping.window_title_contains, "Ryujinx 1.3.3")
            self.assertEqual(resolve_action_key(mapping, "MOVE_FORWARD"), "W")
            self.assertEqual(resolve_action_key(mapping, "confirm"), "ENTER")

    def test_rejects_modifier_and_emergency_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "keyboard.json"
            path.write_text(json.dumps({"mappings": {"MOVE": "CTRL"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_keyboard_mapping(path)
            path.write_text(json.dumps({"mappings": {"MOVE": "F12"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_keyboard_mapping(path)

    def test_hold_duration_is_bounded_and_finite(self):
        self.assertEqual(validate_hold_duration(0.25), 0.25)
        for invalid in (0, 1.01, float("inf"), float("nan")):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_hold_duration(invalid)


class KeyboardControllerTests(unittest.TestCase):
    def test_press_sends_key_down_then_key_up(self):
        sender = FakeSender()
        controller = WindowsKeyboardController(
            sender=sender,
            emergency_stop=FakeStopProbe([False]),
        )
        controller.press("W", 0.05, focus_check=lambda: True)
        self.assertEqual(sender.events[0], ("W", True))
        self.assertEqual(sender.events[-1], ("W", False))
        self.assertEqual(len(sender.events), 2)

    def test_emergency_stop_releases_held_key(self):
        sender = FakeSender()
        controller = WindowsKeyboardController(
            sender=sender,
            emergency_stop=FakeStopProbe([False, True]),
        )
        with self.assertRaises(EmergencyStopRequested):
            controller.press("W", 0.5, focus_check=lambda: True)
        self.assertEqual(sender.events, [("W", True), ("W", False)])

    def test_focus_loss_releases_held_key(self):
        sender = FakeSender()
        focus_values = iter([True, False])
        controller = WindowsKeyboardController(
            sender=sender,
            emergency_stop=FakeStopProbe([False]),
        )
        with self.assertRaises(KeyboardControlError):
            controller.press("W", 0.5, focus_check=lambda: next(focus_values))
        self.assertEqual(sender.events, [("W", True), ("W", False)])


    @unittest.skipUnless(os.name == "nt", "Windows SendInput structure test")
    def test_windows_sender_builds_key_event_without_sending_to_desktop(self):
        sender = WindowsSendInputSender()

        class FakeUser32:
            def __init__(self):
                self.events = []

            def SendInput(self, count, pointer, size):
                event = ctypes.cast(pointer, ctypes.POINTER(_INPUT)).contents
                self.events.append((count, size, event.type, event.ki.wVk, event.ki.dwFlags))
                return 1

        fake_user32 = FakeUser32()
        sender._user32 = fake_user32
        sender.send_key("W", key_down=True)
        sender.send_key("W", key_down=False)
        self.assertEqual(fake_user32.events[0][0], 1)
        self.assertEqual(fake_user32.events[0][2:], (1, ord("W"), 0))
        self.assertEqual(fake_user32.events[1][2:], (1, ord("W"), WindowsSendInputSender.KEYEVENTF_KEYUP))


    def test_no_input_when_emergency_stop_already_pressed(self):
        sender = FakeSender()
        controller = WindowsKeyboardController(
            sender=sender,
            emergency_stop=FakeStopProbe([True]),
        )
        with self.assertRaises(EmergencyStopRequested):
            controller.press("W", 0.1, focus_check=lambda: True)
        self.assertEqual(sender.events, [])


class KeyboardActionTests(unittest.TestCase):
    def _mapping(self, directory: str) -> Path:
        path = Path(directory) / "keyboard.json"
        path.write_text(json.dumps({
            "window_title_contains": "Ryujinx 1.3.3",
            "mappings": {"MOVE_FORWARD": "W"},
        }), encoding="utf-8")
        return path

    def test_action_captures_before_and_after_and_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeWindowProvider()
            controller = FakeController()
            result = perform_mapped_action(
                Settings(log_dir=temp),
                mapping_path=self._mapping(temp),
                action="MOVE_FORWARD",
                duration_seconds=0.25,
                confirmed=True,
                provider=provider,
                controller=controller,
            )
            self.assertEqual(result.key, "W")
            self.assertIsNotNone(result.after_screenshot)
            self.assertEqual(len(provider.captures), 2)
            self.assertEqual(controller.actions, [("W", 0.25)])
            self.assertGreaterEqual(controller.release_calls, 1)
            lines = (Path(temp) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            events = [json.loads(line)["event_type"] for line in lines]
            self.assertIn("keyboard_action_started", events)
            self.assertIn("keyboard_action_completed", events)
            self.assertEqual(events.count("screenshot_captured"), 2)


    def test_action_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeWindowProvider()
            controller = FakeController()
            with self.assertRaises(ValueError):
                perform_mapped_action(
                    Settings(log_dir=temp),
                    mapping_path=self._mapping(temp),
                    action="MOVE_FORWARD",
                    duration_seconds=0.25,
                    confirmed=False,
                    provider=provider,
                    controller=controller,
                )
            self.assertEqual(controller.actions, [])
            self.assertEqual(provider.captures, [])


    def test_action_rejected_if_emulator_is_not_foreground(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeWindowProvider(foreground=False)
            controller = FakeController()
            with self.assertRaises(TargetWindowNotForeground):
                perform_mapped_action(
                    Settings(log_dir=temp),
                    mapping_path=self._mapping(temp),
                    action="MOVE_FORWARD",
                    duration_seconds=0.25,
                    confirmed=True,
                    provider=provider,
                    controller=controller,
                )
            self.assertEqual(controller.actions, [])
            self.assertEqual(provider.captures, [])


if __name__ == "__main__":
    unittest.main()
