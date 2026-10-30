"""Windows game-window discovery and foreground screenshot capture."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


class WindowCaptureError(RuntimeError):
    """Raised when a game window cannot be inspected or captured."""


class UnsupportedPlatformError(WindowCaptureError):
    """Raised when Windows-only capture is requested on another platform."""


class WindowNotForegroundError(WindowCaptureError):
    """Raised when the game window could not be brought to the foreground."""


class WindowMinimizedError(WindowCaptureError):
    """Raised when a minimized game window cannot be captured."""


@dataclass(frozen=True, slots=True)
class WindowInfo:
    handle: int
    title: str
    process_id: int
    left: int
    top: int
    width: int
    height: int
    is_foreground: bool
    is_minimized: bool


class WindowsWindowProvider:
    """Find a titled top-level window and capture its visible client area.

    MSS captures desktop pixels in the window's client rectangle. The target is
    therefore activated before capture so another window cannot silently be
    captured instead. This is not an off-screen or occlusion-proof capture API.
    """

    _FOCUS_SETTLE_SECONDS = 0.35
    _SW_RESTORE = 9

    @staticmethod
    def _handle_value(handle: object) -> int:
        if isinstance(handle, int):
            return handle
        return int(ctypes.cast(handle, ctypes.c_void_p).value or 0)

    @staticmethod
    def _user32():
        if os.name != "nt":
            raise UnsupportedPlatformError("XC2 window observation currently requires Windows.")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
        return user32

    def _read_window(self, hwnd: object, user32) -> WindowInfo | None:
        handle_value = self._handle_value(hwnd)
        title_length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(max(title_length + 1, 2))
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        title = title_buffer.value

        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None

        top_left = wintypes.POINT(rect.left, rect.top)
        bottom_right = wintypes.POINT(rect.right, rect.bottom)
        if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
            return None
        if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
            return None

        width = bottom_right.x - top_left.x
        height = bottom_right.y - top_left.y
        if width <= 0 or height <= 0:
            return None

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        foreground = self._handle_value(user32.GetForegroundWindow())

        return WindowInfo(
            handle=handle_value,
            title=title,
            process_id=int(process_id.value),
            left=int(top_left.x),
            top=int(top_left.y),
            width=int(width),
            height=int(height),
            is_foreground=(foreground == handle_value),
            is_minimized=bool(user32.IsIconic(hwnd)),
        )

    def find_windows(self, title_contains: str) -> list[WindowInfo]:
        if not title_contains.strip():
            raise ValueError("title_contains must not be empty")
        user32 = self._user32()
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)
        matches: list[WindowInfo] = []

        def visit(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            info = self._read_window(hwnd, user32)
            if info and title_contains.casefold() in info.title.casefold():
                matches.append(info)
            return True

        callback = callback_type(visit)
        user32.EnumWindows.argtypes = [callback_type, ctypes.c_ssize_t]
        user32.EnumWindows.restype = wintypes.BOOL
        if not user32.EnumWindows(callback, 0):
            error = ctypes.get_last_error()
            raise WindowCaptureError(f"Windows could not enumerate desktop windows (error {error}).")
        return matches

    def _restore_if_minimised(self, user32, hwnd) -> None:
        """Restore only minimised windows; never unmaximise a visible window."""
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, self._SW_RESTORE)

    def activate_window(self, window: WindowInfo) -> WindowInfo:
        user32 = self._user32()
        hwnd = wintypes.HWND(window.handle)
        # SW_RESTORE also unmaximises a window that is already visible.
        # Only use it for a genuinely minimised window; otherwise preserve
        # the user's current window size and maximised state.
        self._restore_if_minimised(user32, hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(self._FOCUS_SETTLE_SECONDS)
        updated = self._read_window(hwnd, user32)
        if updated is None:
            raise WindowCaptureError("The XC2 game window disappeared before it could be captured.")
        if not updated.is_foreground:
            raise WindowNotForegroundError(
                "Windows did not allow the XC2 game window to become active. "
                "Bring the game to the foreground manually and retry."
            )
        if updated.is_minimized:
            raise WindowMinimizedError("The XC2 game window is still minimized.")
        return updated

    def capture_window(self, window: WindowInfo, output_path: str | Path) -> tuple[int, int]:
        if window.is_minimized:
            raise WindowMinimizedError("The XC2 game window is minimized.")
        if not window.is_foreground:
            raise WindowNotForegroundError(
                "The XC2 game window is not active. Bring it to the foreground and retry."
            )

        try:
            from mss import mss
            from mss.tools import to_png
        except ImportError as exc:
            raise WindowCaptureError(
                "The MSS screenshot dependency is missing. Run "
                "'.\\.venv\\Scripts\\python.exe -m pip install -e .' from the project folder."
            ) from exc

        region = {
            "left": window.left,
            "top": window.top,
            "width": window.width,
            "height": window.height,
        }
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            with mss() as screen:
                virtual_screen = screen.monitors[0]
                right = region["left"] + region["width"]
                bottom = region["top"] + region["height"]
                virtual_right = virtual_screen["left"] + virtual_screen["width"]
                virtual_bottom = virtual_screen["top"] + virtual_screen["height"]
                if (
                    region["left"] < virtual_screen["left"]
                    or region["top"] < virtual_screen["top"]
                    or right > virtual_right
                    or bottom > virtual_bottom
                ):
                    raise WindowCaptureError(
                        "The XC2 game window extends beyond the visible desktop. "
                        "Move it fully onto a monitor and retry."
                    )
                shot = screen.grab(region)
                png_data = to_png(shot.rgb, shot.size)
            output.write_bytes(png_data)
            return int(shot.width), int(shot.height)
        except WindowCaptureError:
            raise
        except Exception as exc:
            raise WindowCaptureError(f"Unable to capture the XC2 game window: {exc}") from exc
