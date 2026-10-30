"""Stage 2C regressions. All senders, windows, clocks and captures are fake."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from xc2bot.actions import TargetWindowNotFound, perform_mapped_action
from xc2bot.cli import main
from xc2bot.config import Settings
from xc2bot.diagnostics import DiagnosticError, DiagnosticStore
from xc2bot.keyboard import KeyboardControlError, WindowsKeyboardController, load_keyboard_mapping
from xc2bot.sequences import ActionSequenceError, SequenceSafetyError, load_action_sequence, run_action_sequence
from test_keyboard import FakeSender, FakeStopProbe
from test_sequences import FakeController, FakeEmergencyStop, FakeWindowProvider


class FakeClock:
    def __init__(self):
        self.time = 0.0
        self.sleeps = []

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.time += seconds


class KeyboardReleaseRegressions(unittest.TestCase):
    def test_interrupt_after_injecting_key_down_still_sends_key_up(self):
        class InterruptedSender(FakeSender):
            def send_key(self, key, *, key_down):
                super().send_key(key, key_down=key_down)
                if key_down:
                    raise KeyboardInterrupt()
        sender = InterruptedSender()
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe())
        with self.assertRaises(KeyboardInterrupt):
            controller.press('W', 0.1, focus_check=lambda: True)
        self.assertEqual(sender.events, [('W', True), ('W', False)])

    def test_error_after_injecting_key_down_still_sends_key_up(self):
        class FailedSender(FakeSender):
            def send_key(self, key, *, key_down):
                super().send_key(key, key_down=key_down)
                if key_down:
                    raise OSError('injection outcome uncertain')
        sender = FailedSender()
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe())
        with self.assertRaises(OSError):
            controller.press('W', 0.1, focus_check=lambda: True)
        self.assertEqual(sender.events, [('W', True), ('W', False)])

    def test_failed_key_up_is_retained_for_cleanup_retry_and_blocks_new_input(self):
        class FailedReleaseSender(FakeSender):
            def send_key(self, key, *, key_down):
                super().send_key(key, key_down=key_down)
                if not key_down and len(self.events) == 2:
                    raise OSError('temporary release failure')
        sender, clock = FailedReleaseSender(), FakeClock()
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe(),
                                               clock=clock.now, sleeper=clock.sleep)
        with self.assertRaises(KeyboardControlError):
            controller.press('W', 0.1, focus_check=lambda: True)
        with self.assertRaises(KeyboardControlError):
            controller.press('D', 0.1, focus_check=lambda: True)
        controller.release_all()
        self.assertEqual(sender.events, [('W', True), ('W', False), ('W', False)])

    def test_interrupt_during_sleep_releases_key(self):
        sender = FakeSender()
        def interrupt(_seconds):
            raise KeyboardInterrupt()
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe(), sleeper=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            controller.press('W', 0.1, focus_check=lambda: True)
        self.assertEqual(sender.events, [('W', True), ('W', False)])


class SequenceSafetyRegressions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mapping_path = self.root / 'keyboard.json'
        self.mapping_path.write_text(json.dumps({'window_title_contains': 'Ryujinx',
                                               'mappings': {'MOVE': 'W', 'RIGHT': 'D'}}), encoding='utf-8')
        self.plan_path = self.root / 'plan.json'
        self.write_plan([{'action': 'MOVE', 'duration_seconds': 0.1},
                         {'action': 'RIGHT', 'duration_seconds': 0.1}])
        self.provider, self.controller, self.clock = FakeWindowProvider(), FakeController(), FakeClock()

    def write_plan(self, steps):
        self.plan_path.write_text(json.dumps({'description': 'Safety test', 'steps': steps}), encoding='utf-8')

    def run_plan(self, **overrides):
        arguments = dict(mapping_path=self.mapping_path, plan_path=self.plan_path, confirmed=True,
                         provider=self.provider, controller=self.controller,
                         emergency_stop=FakeEmergencyStop(), clock=self.clock.now, sleeper=self.clock.sleep)
        arguments.update(overrides)
        return run_action_sequence(Settings(log_dir=str(self.root)), **arguments)

    def events(self):
        return [json.loads(line) for line in (self.root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_invalid_plan_shapes_and_numbers_rejected_before_window_access(self):
        invalid = [None, [], {}, {'steps': []}, {'description': '', 'steps': [{}]},
                   {'steps': [{}]}, {'steps': ['MOVE']},
                   {'steps': [{'action': 'MOVE', 'duration_seconds': 0.1, 'extra': 1}]},
                   {'extra': 1, 'steps': []}]
        for field in ('duration_seconds', 'delay_after_seconds'):
            for value in (True, '0.1', None, float('nan'), float('inf'), -1, 10**400):
                step = {'action': 'MOVE', 'duration_seconds': 0.1, field: value}
                invalid.append({'steps': [step]})
        invalid.extend([{'steps': [{'action': 'MOVE', 'duration_seconds': x}]} for x in (0, 0.049, 1.001)])
        invalid.append({'steps': [{'action': 'MOVE', 'duration_seconds': 0.1, 'delay_after_seconds': 0.1}]})
        with patch.object(self.provider, 'find_windows', side_effect=AssertionError('must validate first')):
            for plan in invalid:
                with self.subTest(plan=plan):
                    self.plan_path.write_text(json.dumps(plan), encoding='utf-8')
                    with self.assertRaises(ActionSequenceError):
                        self.run_plan()
        self.assertEqual(self.controller.actions, [])

    def test_maximum_limits_are_inclusive(self):
        self.write_plan([{'action': 'MOVE', 'duration_seconds': 0.625,
                          'delay_after_seconds': 2 if i < 5 else 0} for i in range(8)])
        plan = load_action_sequence(self.plan_path, load_keyboard_mapping(self.mapping_path))
        self.assertEqual((len(plan.steps), plan.total_hold_seconds, plan.total_delay_seconds, plan.planned_seconds),
                         (8, 5, 10, 15))

    def test_excessive_total_delay_is_rejected(self):
        self.write_plan([{'action': 'MOVE', 'duration_seconds': 0.05,
                          'delay_after_seconds': 2 if i < 6 else 0} for i in range(8)])
        with self.assertRaises(ActionSequenceError):
            self.run_plan()
        self.assertEqual(self.controller.actions, [])

    def test_excessive_step_delay_is_rejected(self):
        self.write_plan([{'action': 'MOVE', 'duration_seconds': 0.05, 'delay_after_seconds': 2.01},
                         {'action': 'MOVE', 'duration_seconds': 0.05}])
        with self.assertRaises(ActionSequenceError):
            self.run_plan()

    def test_duplicate_json_fields_rejected_in_plans_and_mappings(self):
        self.plan_path.write_text('{"steps": [], "steps": []}', encoding='utf-8')
        with self.assertRaises(ActionSequenceError):
            self.run_plan()
        self.mapping_path.write_text('{"mappings":{"MOVE":"W","MOVE":"D"}}', encoding='utf-8')
        with self.assertRaises(ValueError):
            load_keyboard_mapping(self.mapping_path)

    def test_emergency_already_pressed_prevents_activation_capture_and_input(self):
        with patch.object(self.provider, 'activate_window') as activate:
            with self.assertRaises(SequenceSafetyError):
                self.run_plan(emergency_stop=FakeEmergencyStop([True]))
        activate.assert_not_called()
        self.assertEqual(self.provider.captures, [])
        self.assertEqual(self.controller.actions, [])

    def test_mapping_is_not_reloaded_between_actions(self):
        original = self.controller.press
        def press(*args, **kwargs):
            original(*args, **kwargs)
            self.mapping_path.write_text('{"mappings":{"MOVE":"ENTER","RIGHT":"ENTER"}}', encoding='utf-8')
        self.controller.press = press
        self.assertEqual(self.run_plan().status, 'completed')
        self.assertEqual(self.controller.actions, [('W', 0.1), ('D', 0.1)])

    def test_target_change_during_step_setup_is_rejected_before_input(self):
        calls = []
        def action(*args, **kwargs):
            calls.append(None)
            if len(calls) == 2:
                self.provider.window = replace(self.provider.window, handle=999, process_id=999)
            return perform_mapped_action(*args, **kwargs)
        with patch('xc2bot.sequences.perform_mapped_action', side_effect=action):
            with self.assertRaises(TargetWindowNotFound):
                self.run_plan()
        self.assertEqual(self.controller.actions, [('W', 0.1)])

    def test_same_process_new_window_is_not_accepted(self):
        original = self.provider.capture_window
        def capture(*args):
            result = original(*args)
            if len(self.provider.captures) == 2:
                self.provider.window = replace(self.provider.window, handle=999)
            return result
        self.provider.capture_window = capture
        with self.assertRaises(SequenceSafetyError):
            self.run_plan()
        self.assertEqual(self.controller.actions, [('W', 0.1)])

    def test_focus_loss_is_not_reactivated_mid_sequence(self):
        self.provider.lose_focus_after_capture = 2
        with patch.object(self.provider, 'activate_window') as activate:
            with self.assertRaises(SequenceSafetyError):
                self.run_plan()
        activate.assert_not_called()
        self.assertEqual(self.controller.actions, [('W', 0.1)])

    def test_initial_activation_is_allowed_once(self):
        self.provider.window = replace(self.provider.window, is_foreground=False)
        with patch.object(self.provider, 'activate_window', wraps=self.provider.activate_window) as activate:
            self.run_plan()
        self.assertEqual(activate.call_count, 1)

    def test_before_capture_failure_sends_no_key(self):
        with patch.object(self.provider, 'capture_window', side_effect=OSError('capture failed')):
            with self.assertRaises(OSError):
                self.run_plan()
        self.assertEqual(self.controller.actions, [])
        self.assertIn('sequence_aborted', [e['event_type'] for e in self.events()])

    def test_after_capture_failure_aborts_remaining_steps_as_partial(self):
        original = self.provider.capture_window
        def capture(*args):
            if len(self.provider.captures) == 1:
                raise OSError('capture failed')
            return original(*args)
        self.provider.capture_window = capture
        with self.assertRaises(SequenceSafetyError):
            self.run_plan()
        self.assertEqual(self.controller.actions, [('W', 0.1)])
        with sqlite3.connect(self.root / 'sessions.sqlite3') as db:
            rows = db.execute('select status from sessions').fetchall()
        self.assertEqual(rows, [('partial',), ('partial',)])

    def test_runtime_limit_after_final_capture_aborts(self):
        original = self.provider.capture_window
        def capture(*args):
            result = original(*args)
            if len(self.provider.captures) == 4:
                self.clock.sleep(45)
            return result
        self.provider.capture_window = capture
        with self.assertRaises(SequenceSafetyError):
            self.run_plan()
        self.assertNotIn('sequence_completed', [e['event_type'] for e in self.events()])

    def test_runtime_limit_during_hold_releases_key(self):
        sender = FakeSender()
        def sleep(_seconds):
            self.clock.time = 45
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe(),
                                               clock=self.clock.now, sleeper=sleep)
        with self.assertRaises(SequenceSafetyError):
            self.run_plan(controller=controller)
        self.assertEqual(sender.events, [('W', True), ('W', False)])

    def test_shared_emergency_probe_is_used_by_default_controller(self):
        sender = FakeSender()
        class StopDuringHold:
            def is_pressed(self):
                return bool(sender.events)
        stop = StopDuringHold()
        def controller_factory(**kwargs):
            self.assertIs(kwargs['emergency_stop'], stop)
            return WindowsKeyboardController(sender=sender, **kwargs)
        with patch('xc2bot.sequences.WindowsKeyboardController', side_effect=controller_factory):
            with self.assertRaises(KeyboardControlError):
                self.run_plan(controller=None, emergency_stop=stop)
        self.assertEqual(sender.events, [('W', True), ('W', False)])

    def test_interrupt_links_failed_child_and_finalizes_both_sessions(self):
        class InterruptedSender(FakeSender):
            def send_key(self, key, *, key_down):
                super().send_key(key, key_down=key_down)
                if key_down:
                    raise KeyboardInterrupt()
        sender = InterruptedSender()
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe())
        with self.assertRaises(KeyboardInterrupt):
            self.run_plan(controller=controller)
        self.assertEqual(sender.events, [('W', True), ('W', False)])
        events = self.events()
        start = next(e for e in events if e['event_type'] == 'sequence_step_started')
        linked = next(e for e in events if e['event_type'] == 'action_session_linked')
        self.assertEqual(start['details']['action_session_id'], linked['session_id'])
        self.assertEqual(linked['details']['parent_sequence_id'], start['session_id'])
        with sqlite3.connect(self.root / 'sessions.sqlite3') as db:
            rows = db.execute('select ended_at, status from sessions').fetchall()
            count = db.execute('select count(*) from events').fetchone()[0]
        self.assertTrue(all(ended and status == 'interrupted' for ended, status in rows))
        self.assertEqual(count, len(events))

    def test_diagnostics_failure_after_key_down_releases_and_stops(self):
        original = DiagnosticStore.log_event
        def log(store, event, **kwargs):
            if event == 'keyboard_action_completed':
                raise DiagnosticError('simulated database write failure')
            return original(store, event, **kwargs)
        sender = FakeSender()
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe(),
                                               clock=self.clock.now, sleeper=self.clock.sleep)
        with patch.object(DiagnosticStore, 'log_event', log):
            with self.assertRaises(DiagnosticError):
                self.run_plan(controller=controller)
        self.assertEqual(sender.events, [('W', True), ('W', False)])

    def test_stop_during_inter_step_wait(self):
        self.write_plan([{'action': 'MOVE', 'duration_seconds': 0.1, 'delay_after_seconds': 0.2},
                         {'action': 'RIGHT', 'duration_seconds': 0.1}])
        clock = self.clock
        class StopInWait:
            def is_pressed(self):
                return clock.time >= 0.05
        with self.assertRaises(SequenceSafetyError):
            self.run_plan(emergency_stop=StopInWait())
        self.assertEqual(self.controller.actions, [('W', 0.1)])
        self.assertEqual(self.clock.sleeps, [0.05])

    def test_focus_loss_during_inter_step_wait(self):
        self.write_plan([{'action': 'MOVE', 'duration_seconds': 0.1, 'delay_after_seconds': 0.2},
                         {'action': 'RIGHT', 'duration_seconds': 0.1}])
        def sleep(seconds):
            self.clock.sleep(seconds)
            self.provider.window = replace(self.provider.window, is_foreground=False)
        with self.assertRaises(SequenceSafetyError):
            self.run_plan(sleeper=sleep)
        self.assertEqual(self.controller.actions, [('W', 0.1)])

    def test_cli_help_and_required_arguments_do_not_send_input(self):
        with patch('xc2bot.cli.run_action_sequence') as run, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(['sequence', '--help'])
            self.assertEqual(caught.exception.code, 0)
            for args in (['sequence'], ['sequence', '--mapping', 'x', '--plan', 'y']):
                with self.assertRaises(SystemExit) as caught:
                    main(args)
                self.assertEqual(caught.exception.code, 2)
        run.assert_not_called()

    def test_cli_invalid_plan_returns_failure_without_input(self):
        self.write_plan([])
        with contextlib.redirect_stderr(io.StringIO()), patch('xc2bot.sequences.WindowsWindowProvider') as provider:
            self.assertEqual(main(['sequence', '--mapping', str(self.mapping_path),
                                   '--plan', str(self.plan_path), '--confirm']), 1)
        provider.assert_not_called()

    def test_sequence_orders_releases_captures_and_delays_with_one_clock(self):
        self.write_plan([{'action': 'MOVE', 'duration_seconds': 0.1, 'delay_after_seconds': 0.2},
                         {'action': 'RIGHT', 'duration_seconds': 0.1}])
        sender = FakeSender()
        original = self.provider.capture_window
        def capture(*args):
            # Both before and after images must be taken with no injected key held.
            self.assertTrue(not sender.events or sender.events[-1][1] is False)
            return original(*args)
        self.provider.capture_window = capture
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe(),
                                               clock=self.clock.now, sleeper=self.clock.sleep)
        result = self.run_plan(controller=controller)
        self.assertEqual(sender.events, [('W', True), ('W', False), ('D', True), ('D', False)])
        self.assertAlmostEqual(result.elapsed_seconds, 0.4)
        self.assertEqual(len(self.provider.captures), 4)
        events = self.events()
        with sqlite3.connect(self.root / 'sessions.sqlite3') as db:
            self.assertEqual(db.execute('select count(*) from events').fetchone()[0], len(events))
            rows = db.execute('select ended_at, status from sessions').fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(ended and status == 'completed' for ended, status in rows))
        self.assertEqual(len([e for e in events if e['event_type'] == 'action_session_linked']), 2)

    def test_persistent_key_up_failure_aborts_and_records_release_failure(self):
        class FailedReleaseSender(FakeSender):
            def send_key(self, key, *, key_down):
                super().send_key(key, key_down=key_down)
                if not key_down:
                    raise OSError('release denied')
        sender = FailedReleaseSender()
        controller = WindowsKeyboardController(sender=sender, emergency_stop=FakeStopProbe(),
                                               clock=self.clock.now, sleeper=self.clock.sleep)
        with self.assertRaises(KeyboardControlError):
            self.run_plan(controller=controller)
        self.assertEqual(sum(down for key, down in sender.events), 1)
        self.assertLessEqual(len(sender.events), 5)  # bounded defensive cleanup, never another action
        self.assertIn('keyboard_release_failed', [e['event_type'] for e in self.events()])
        with self.assertRaises(KeyboardControlError):
            controller.press('D', 0.1, focus_check=lambda: True)

    def test_explicit_empty_title_is_rejected(self):
        for title in ('', ' '):
            with self.subTest(title=title), self.assertRaises(ActionSequenceError):
                self.run_plan(window_title=title)
        self.assertEqual(self.controller.actions, [])
