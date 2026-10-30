from __future__ import annotations

import unittest

from xc2bot.window_capture import WindowsWindowProvider


class FakeUser32:
    def __init__(self, *, minimised: bool) -> None:
        self.minimised = minimised
        self.restore_calls: list[tuple[int, int]] = []

    def IsIconic(self, hwnd) -> bool:
        return self.minimised

    def ShowWindow(self, hwnd, command: int) -> bool:
        self.restore_calls.append((int(hwnd), command))
        return True


class WindowActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = WindowsWindowProvider()

    def test_visible_normal_window_is_not_resized_during_activation(self) -> None:
        user32 = FakeUser32(minimised=False)

        self.provider._restore_if_minimised(user32, 123)

        self.assertEqual(user32.restore_calls, [])

    def test_visible_maximised_window_is_not_unmaximised_during_activation(self) -> None:
        # A maximised, visible window is not iconic. SW_RESTORE must not be
        # called merely because another application currently has focus.
        user32 = FakeUser32(minimised=False)

        self.provider._restore_if_minimised(user32, 456)

        self.assertEqual(user32.restore_calls, [])

    def test_minimised_window_is_restored_before_activation(self) -> None:
        user32 = FakeUser32(minimised=True)

        self.provider._restore_if_minimised(user32, 789)

        self.assertEqual(
            user32.restore_calls,
            [(789, self.provider._SW_RESTORE)],
        )


if __name__ == "__main__":
    unittest.main()
