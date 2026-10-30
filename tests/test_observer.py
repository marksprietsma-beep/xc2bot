from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from xc2bot.config import Settings
from xc2bot.observer import (
    AmbiguousGameWindowError,
    GameWindowNotForegroundError,
    GameWindowNotFoundError,
    observe_once,
)
from xc2bot.window_capture import WindowInfo


class FakeWindowProvider:
    def __init__(self, windows: list[WindowInfo] | None = None):
        self.windows = windows or []
        self.activated: list[int] = []
        self.captured: list[int] = []

    def find_windows(self, title_contains: str) -> list[WindowInfo]:
        return [
            item for item in self.windows
            if title_contains.casefold() in item.title.casefold()
        ]

    def activate_window(self, window: WindowInfo) -> WindowInfo:
        self.activated.append(window.handle)
        return replace(window, is_foreground=True, is_minimized=False)

    def capture_window(self, window: WindowInfo, output_path: str | Path) -> tuple[int, int]:
        self.captured.append(window.handle)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x89PNG\r\n\x1a\nFAKE-PNG")
        return window.width, window.height


def game_window(
    *,
    handle: int = 100,
    title: str = "Ryujinx - Xenoblade Chronicles 2 v2.1.0",
    foreground: bool = True,
    minimized: bool = False,
) -> WindowInfo:
    return WindowInfo(
        handle=handle,
        title=title,
        process_id=1234 + handle,
        left=10,
        top=20,
        width=1280,
        height=720,
        is_foreground=foreground,
        is_minimized=minimized,
    )


class ObserverTests(unittest.TestCase):
    def test_observe_once_captures_window_and_records_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeWindowProvider([game_window(foreground=False)])
            result = observe_once(Settings(log_dir=temp), provider=provider)

            screenshot = Path(result.screenshot_path)
            self.assertTrue(screenshot.is_file())
            self.assertEqual(screenshot.read_bytes(), b"\x89PNG\r\n\x1a\nFAKE-PNG")
            self.assertEqual((result.width, result.height), (1280, 720))
            self.assertEqual(provider.activated, [100])
            self.assertEqual(provider.captured, [100])

            event_lines = (Path(temp) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(event_lines), 1)
            event = json.loads(event_lines[0])
            self.assertEqual(event["event_type"], "screenshot_captured")
            self.assertEqual(event["details"]["filename"], screenshot.name)
            self.assertEqual(event["details"]["width"], 1280)

    def test_no_matching_game_window_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(GameWindowNotFoundError):
                observe_once(Settings(log_dir=temp), provider=FakeWindowProvider([]))

    def test_multiple_windows_without_unique_foreground_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeWindowProvider([
                game_window(handle=100, foreground=False),
                game_window(handle=200, foreground=False),
            ])
            with self.assertRaises(AmbiguousGameWindowError):
                observe_once(Settings(log_dir=temp), provider=provider)
            self.assertEqual(provider.captured, [])

    def test_unique_foreground_window_is_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeWindowProvider([
                game_window(handle=100, foreground=False),
                game_window(handle=200, foreground=True),
            ])
            result = observe_once(Settings(log_dir=temp), provider=provider)
            self.assertEqual(result.window_handle, 200)
            self.assertEqual(provider.captured, [200])

    def test_no_activate_requires_foreground_window(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeWindowProvider([game_window(foreground=False)])
            with self.assertRaises(GameWindowNotForegroundError):
                observe_once(Settings(log_dir=temp), provider=provider, activate=False)
            self.assertEqual(provider.captured, [])

    def test_screenshot_is_not_written_when_activation_fails(self):
        class FailingProvider(FakeWindowProvider):
            def activate_window(self, window: WindowInfo) -> WindowInfo:
                raise GameWindowNotForegroundError("activation denied")

        with tempfile.TemporaryDirectory() as temp:
            provider = FailingProvider([game_window(foreground=False)])
            with self.assertRaises(GameWindowNotForegroundError):
                observe_once(Settings(log_dir=temp), provider=provider)
            self.assertEqual(provider.captured, [])


if __name__ == "__main__":
    unittest.main()
