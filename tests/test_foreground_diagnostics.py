"""Read-only foreground telemetry; no real desktop activation, capture or input."""
from __future__ import annotations

import contextlib
import ctypes
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from xc2bot.actions import TargetWindowNotForeground, perform_mapped_action
from xc2bot.config import Settings
from xc2bot.diagnostics import DiagnosticError, DiagnosticStore
from xc2bot.keyboard import WindowsKeyboardController
from xc2bot.sequences import run_action_sequence
from xc2bot.window_capture import ForegroundWindowInfo, WindowsWindowProvider
from test_keyboard import FakeSender, FakeStopProbe
from test_sequence_safety import FakeClock
from test_sequences import FakeController, FakeEmergencyStop, FakeWindowProvider


class ForegroundReaderTests(unittest.TestCase):
    def user32(self, handles=(333, 333), *, title='Unrelated foreground window', pid=444):
        api = Mock()
        api.GetForegroundWindow.side_effect = handles
        api.GetWindowTextLengthW.return_value = len(title)
        def read_title(hwnd, buffer, length):
            self.assertEqual(hwnd, 333)
            buffer.value = title
            return len(title)
        def read_pid(hwnd, output):
            output._obj.value = pid
            return 77 if pid else 0
        api.GetWindowTextW.side_effect = read_title
        api.GetWindowThreadProcessId.side_effect = read_pid
        return api

    def test_reads_non_target_identity_without_activation_geometry_or_dpi_calls(self):
        api = self.user32()
        provider = WindowsWindowProvider()
        with patch.object(provider, '_foreground_user32', return_value=api), \
                patch.object(provider, '_user32', side_effect=AssertionError('not read-only')), \
                patch.object(provider, '_read_window', side_effect=AssertionError('no client geometry')):
            result = provider.get_foreground_window()
        self.assertEqual(result, ForegroundWindowInfo(333, 444, 'Unrelated foreground window'))
        self.assertEqual([call[0] for call in api.mock_calls],
                         ['GetForegroundWindow', 'GetWindowTextLengthW', 'GetWindowTextW',
                          'GetWindowThreadProcessId', 'GetForegroundWindow'])

    def test_null_foreground_is_explicit_and_does_not_query_window_zero(self):
        api = self.user32(handles=(None,))
        with patch.object(WindowsWindowProvider, '_foreground_user32', return_value=api):
            self.assertEqual(WindowsWindowProvider().get_foreground_window(),
                             ForegroundWindowInfo(0, None, None))
        self.assertEqual([call[0] for call in api.mock_calls], ['GetForegroundWindow'])

    def test_focus_change_during_metadata_read_is_marked(self):
        api = self.user32(handles=(333, 999))
        with patch.object(WindowsWindowProvider, '_foreground_user32', return_value=api):
            result = WindowsWindowProvider().get_foreground_window()
        self.assertEqual(result.handle, 333)
        self.assertTrue(result.changed_during_read)

    def test_empty_title_and_unavailable_pid_remain_explicit(self):
        api = self.user32(title='', pid=0)
        with patch.object(WindowsWindowProvider, '_foreground_user32', return_value=api):
            self.assertEqual(WindowsWindowProvider().get_foreground_window(),
                             ForegroundWindowInfo(333, None, ''))

    def test_native_reader_initializes_only_read_apis(self):
        api = self.user32()
        # Binding native signatures is tested with a fake DLL even on non-Windows hosts.
        with patch('xc2bot.window_capture.os.name', 'nt'), \
                patch('xc2bot.window_capture.ctypes.WinDLL', create=True, return_value=api) as dll:
            self.assertIs(WindowsWindowProvider._foreground_user32(), api)
        dll.assert_called_once_with('user32', use_last_error=True)
        self.assertEqual(api.mock_calls, [])
        self.assertEqual(api.GetForegroundWindow.argtypes, [])
        self.assertEqual(api.GetWindowThreadProcessId.restype, ctypes.wintypes.DWORD)


class ForegroundCheckpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mapping = self.root / 'mapping.json'
        self.mapping.write_text(json.dumps({'window_title_contains': 'Ryujinx', 'mappings': {
            'MOVE_FORWARD': 'W', 'MOVE_RIGHT': 'D', 'MOVE_LEFT': 'A'}}), encoding='utf-8')
        self.settings = Settings(log_dir=str(self.root))
        self.provider, self.controller = FakeWindowProvider(), FakeController()

    def action(self, **overrides):
        arguments = dict(mapping_path=self.mapping, action='MOVE_FORWARD', duration_seconds=0.1,
                         confirmed=True, provider=self.provider, controller=self.controller)
        arguments.update(overrides)
        return perform_mapped_action(self.settings, **arguments)

    def events(self):
        return [json.loads(line) for line in (self.root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_checkpoints_bracket_both_captures_input_and_completed_action(self):
        timeline = []
        original_log = DiagnosticStore.log_event
        def log(store, event, **kwargs):
            if event == 'foreground_checkpoint':
                timeline.append((kwargs['details']['checkpoint'], kwargs['details']['capture_phase']))
            return original_log(store, event, **kwargs)
        original_capture = self.provider.capture_window
        def capture(*args):
            timeline.append(('capture', None))
            return original_capture(*args)
        original_press = self.controller.press
        def press(*args, **kwargs):
            timeline.append(('press', None))
            return original_press(*args, **kwargs)
        self.provider.capture_window, self.controller.press = capture, press
        with patch.object(DiagnosticStore, 'log_event', log), \
                patch.object(self.provider, 'activate_window', wraps=self.provider.activate_window) as activate:
            self.action()
        activate.assert_not_called()
        self.assertEqual(timeline, [
            ('before_screenshot', 'before'), ('capture', None), ('after_screenshot', 'before'),
            ('before_keyboard_input', None), ('press', None), ('after_action', None),
            ('before_screenshot', 'after'), ('capture', None), ('after_screenshot', 'after')])
        checkpoints = [e for e in self.events() if e['event_type'] == 'foreground_checkpoint']
        self.assertEqual(len(checkpoints), 6)
        for event in checkpoints:
            details = event['details']
            self.assertEqual(details['target'], {'handle': 101, 'process_id': 202, 'title': 'Ryujinx 1.3.3'})
            self.assertEqual(details['foreground'], {'handle': 101, 'process_id': 202,
                                                    'title': 'Ryujinx 1.3.3', 'changed_during_read': False})
            self.assertIn('observed_at', details)
            self.assertEqual(event['result'], 'observed')

    def test_fourth_before_capture_focus_loss_records_other_window_without_fourth_key(self):
        plan = self.root / 'sequence.json'
        plan.write_text(json.dumps({'steps': [{'action': action, 'duration_seconds': 0.1}
                        for action in ['MOVE_FORWARD', 'MOVE_RIGHT', 'MOVE_FORWARD', 'MOVE_LEFT']]}), encoding='utf-8')
        self.provider.window = replace(self.provider.window, is_foreground=False)
        self.provider.lose_focus_after_capture = 7
        with patch.object(self.provider, 'activate_window', wraps=self.provider.activate_window) as activate:
            with self.assertRaises(TargetWindowNotForeground):
                run_action_sequence(self.settings, mapping_path=self.mapping, plan_path=plan, confirmed=True,
                                    provider=self.provider, controller=self.controller,
                                    emergency_stop=FakeEmergencyStop())
        self.assertEqual(activate.call_count, 1)
        self.assertEqual(self.controller.actions, [('W', 0.1), ('D', 0.1), ('W', 0.1)])
        events = self.events()
        starts = [e for e in events if e['event_type'] == 'sequence_step_started']
        fourth_id = starts[3]['details']['action_session_id']
        fourth = [e for e in events if e['session_id'] == fourth_id and e['event_type'] == 'foreground_checkpoint']
        self.assertEqual([e['details']['checkpoint'] for e in fourth],
                         ['before_screenshot', 'after_screenshot', 'before_keyboard_input', 'action_failed'])
        self.assertEqual(fourth[0]['details']['foreground']['handle'], 101)
        for event in fourth[1:]:
            self.assertEqual(event['details']['foreground']['handle'], 999)
            self.assertEqual(event['details']['foreground']['process_id'], 888)
            self.assertEqual(event['details']['foreground']['title'], 'Other window')
            self.assertEqual(event['details']['target']['handle'], 101)
            self.assertEqual(event['details']['parent_sequence_id'], starts[0]['session_id'])
        self.assertEqual(next(e for e in events if e['event_type'] == 'sequence_aborted')['details']['steps_completed'], 3)
        with contextlib.closing(sqlite3.connect(self.root / 'sessions.sqlite3')) as db:
            stored = [json.loads(row[0]) for row in db.execute(
                "SELECT details_json FROM events WHERE session_id=? AND event_type='foreground_checkpoint' ORDER BY id",
                (fourth_id,))]
        self.assertEqual(stored, [e['details'] for e in fourth])

    def test_stale_checkpoint_cannot_override_the_existing_pre_input_guard(self):
        original = DiagnosticStore.log_event
        def log(store, event, **kwargs):
            result = original(store, event, **kwargs)
            if event == 'foreground_checkpoint' and kwargs['details']['checkpoint'] == 'before_keyboard_input':
                self.provider.window = replace(self.provider.window, is_foreground=False)
            return result
        with patch.object(DiagnosticStore, 'log_event', log):
            with self.assertRaises(TargetWindowNotForeground):
                self.action()
        self.assertEqual(self.controller.actions, [])
        failure = next(e for e in self.events() if e['event_type'] == 'foreground_checkpoint'
                       and e['details']['checkpoint'] == 'action_failed')
        self.assertEqual(failure['details']['foreground']['handle'], 999)

    def test_unavailable_foreground_observation_does_not_replace_focus_checks(self):
        with patch.object(self.provider, 'get_foreground_window', side_effect=OSError('unavailable')):
            self.action()
        checkpoints = [e for e in self.events() if e['event_type'] == 'foreground_checkpoint']
        self.assertTrue(checkpoints)
        self.assertTrue(all(e['result'] == 'unavailable' and e['details']['foreground'] is None for e in checkpoints))
        self.assertEqual(self.controller.actions, [('W', 0.1)])

    def test_completed_action_reports_other_foreground_before_after_capture_is_skipped(self):
        original = self.controller.press
        def press(*args, **kwargs):
            original(*args, **kwargs)
            self.provider.window = replace(self.provider.window, is_foreground=False)
        self.controller.press = press
        with patch.object(self.provider, 'activate_window', wraps=self.provider.activate_window) as activate:
            result = self.action()
        activate.assert_not_called()
        self.assertIsNone(result.after_screenshot)
        self.assertEqual(self.controller.actions, [('W', 0.1)])
        after = next(e for e in self.events() if e['event_type'] == 'foreground_checkpoint'
                     and e['details']['checkpoint'] == 'after_action')
        self.assertEqual(after['details']['foreground']['handle'], 999)
        self.assertEqual(after['details']['target']['handle'], 101)

    def test_diagnostic_write_failure_stops_before_capture_or_input(self):
        original = DiagnosticStore.log_event
        def log(store, event, **kwargs):
            if event == 'foreground_checkpoint':
                raise DiagnosticError('write failed')
            return original(store, event, **kwargs)
        with patch.object(DiagnosticStore, 'log_event', log):
            with self.assertRaises(DiagnosticError):
                self.action()
        self.assertEqual(self.provider.captures, [])
        self.assertEqual(self.controller.actions, [])
        self.assertGreaterEqual(self.controller.release_calls, 1)

    def test_foreground_titles_are_redacted_in_jsonl_and_sqlite(self):
        private = ForegroundWindowInfo(999, 888, r'Terminal C:\Users\Mark\private.txt Bearer sample-token')
        with patch.object(self.provider, 'get_foreground_window', return_value=private):
            self.action()
        text = (self.root / 'events.jsonl').read_text(encoding='utf-8')
        with contextlib.closing(sqlite3.connect(self.root / 'sessions.sqlite3')) as db:
            text += str(db.execute('SELECT details_json FROM events').fetchall())
        self.assertNotIn('Mark', text)
        self.assertNotIn('sample-token', text)
        self.assertIn('<PATH>', text)
        self.assertIn('[REDACTED]', text)

    def test_successful_hold_has_no_telemetry_during_key_down_and_same_duration(self):
        sender, clock = FakeSender(), FakeClock()
        original = DiagnosticStore.log_event
        def log(store, event, **kwargs):
            if event == 'foreground_checkpoint':
                self.assertTrue(not sender.events or sender.events[-1][1] is False)
            return original(store, event, **kwargs)
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe(),
                                               clock=clock.now, sleeper=clock.sleep)
        with patch.object(DiagnosticStore, 'log_event', log):
            self.action(controller=controller)
        self.assertEqual(sender.events, [('W', True), ('W', False)])
        self.assertAlmostEqual(clock.time, 0.1)
        self.assertTrue(all(delay <= 0.02 for delay in clock.sleeps))
