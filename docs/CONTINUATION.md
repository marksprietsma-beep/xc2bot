# Continuation checkpoint

## Completed in Stage 1 foundation

- Python package, configuration and CLI.
- Deterministic state machine and mock emulator/controller/screenshot interfaces.
- Safety limits for session duration, consecutive errors, retries and action timing.
- JSONL event log and SQLite session/event store.
- Diagnostic ZIP export with five files; screenshots excluded by default and likely secrets/absolute paths redacted.
- Offline tests for configuration, transitions, mocks, safety, persistence, redaction, export and simulated encounter.
- Original Stage 1's 19-test suite passed on Mark's Windows laptop.

## Completed in Stage 2A — read-only observer

- Windows-only top-level window discovery.
- Window client-area geometry, process ID, foreground and minimized-state reporting.
- One-shot foreground activation and screenshot capture using MSS.
- PNG saved under artifacts/logs/screenshots by default.
- screenshot_captured event persisted to JSONL and SQLite.
- xc2bot windows and xc2bot observe CLI commands.
- Mark confirmed a real XC2 screenshot from Ryujinx at 1280 × 760.

## Implemented in Stage 2B — bounded manual keyboard input

- Local JSON semantic action-to-key mapping with validation and an example template.
- Windows SendInput adapter for single virtual-key down/up events.
- No modifier keys, Windows keys, F12, or key combinations are accepted in the mapping.
- Each action requires explicit --confirm and a 0.05–1.00 second hold duration.
- The command automatically restores/activates the uniquely matched Ryujinx game window before input, so it can be launched from PowerShell without manually switching windows.
- Foreground focus is rechecked during the hold.
- Ctrl+Alt+F12 is polled as a physical emergency-stop chord.
- Held keys are released in a finally cleanup path and again defensively by the action service.
- Before/after screenshots and structured events are recorded in the same diagnostic session.
- xc2bot controls and xc2bot input CLI commands added.
- Unit tests cover mapping validation, duration limits, key-up cleanup, emergency stop, focus loss, screenshot capture and diagnostics.

## Stage 2B safety boundary

- This is a manually invoked one-key action, not an unattended loop.
- One action per command; no automatic repeats or macros.
- Maximum key hold is hard-coded to one second.
- No modifier combinations; Ctrl+Alt+F12 is reserved for emergency stop.
- The emulator is activated automatically before input. If Windows denies activation, the command fails closed without sending a key.
- No game-state recognition, combat logic, target selection, farming loop or story progression.
- Screenshots remain outside diagnostic ZIP exports by default.
- Real SendInput behaviour and emergency-stop detection still require acceptance testing on Mark's Windows laptop.

## Windows acceptance procedure

1. Run the full test suite with Python 3.12.
2. Copy config/keyboard.example.json to the git-ignored config/keyboard.json.
3. Verify each mapping against the actual Ryujinx Input settings.
4. Unpause XC2 and move to a safe location.
5. Run xc2bot controls --mapping config/keyboard.json.
6. Run one short action from PowerShell; the command automatically activates the Ryujinx game window: xc2bot input --mapping config/keyboard.json --action MOVE_FORWARD --duration 0.25 --confirm.
7. Inspect both screenshots and the matching JSONL/SQLite events.

## Next steps

1. Confirm Windows CI passes for Stage 2B.
2. Have Mark run the updated test suite and configure the local keyboard mapping.
3. Validate one 0.25-second movement action and its before/after screenshots.
4. Only after manual input is reliable, consider a small sequence of individually bounded actions.
5. Collect representative screenshots before implementing game-state recognition.
6. Keep story progression under user control; stop on uncertain state.


## Implemented in Stage 2C — bounded manual action sequences

- Added xc2bot sequence --mapping ... --plan ... --confirm.
- Added strict JSON plan validation before any emulator window is activated or input is sent.
- A plan contains 1–8 named semantic actions; each action is a single mapped key, held for 0.05–1.00 seconds.
- Hard limits: 5 seconds aggregate key-hold time, 2 seconds per inter-step delay, 10 seconds aggregate delay, 15 seconds planned duration and 45 seconds measured runtime.
- Requires explicit operator confirmation; there are no loops, retries, simultaneous keypresses or unbounded macros.
- Every action reuses the Stage 2B activation, focus-check, emergency-stop, key-release and before/after screenshot path.
- A parent sequence diagnostic session records each step and links it to the individual child action session IDs and screenshot filenames.
- Emergency stop and focus are checked between steps and during inter-step waits; focus loss or stop cancels all remaining steps.
- Added config/sequence.example.json for a short four-direction movement test.
- Added unit tests for plan validation, action execution, screenshots, linked diagnostics, explicit confirmation, focus-loss abort and emergency-stop abort.

## Stage 2C Windows acceptance procedure

1. Update the local project from recovery/stage-1-foundation and retain .venv and config/keyboard.json.
2. Run .\\.venv\\Scripts\\python.exe -m xc2bot test.
3. Launch XC2, load a save and move Rex to a clear, safe area.
4. Run .\\.venv\\Scripts\\python.exe -m xc2bot sequence --mapping config\\keyboard.json --plan config\\sequence.example.json --confirm.
5. Verify that each of the four short movements occurs, that the sequence stops after one pass, and that the output lists four child action results.
6. Inspect the parent sequence session and child sessions in artifacts\\logs\\events.jsonl, sessions.sqlite3, and the before/after screenshots.
7. If the character does not move as expected, do not repeat the sequence; inspect the screenshots and logs first.

## Stage 2C safety boundary

Stage 2C is a manually initiated, one-pass action sequence only. It does not observe game state, make decisions, select enemies, fight, farm, loop, retry, or progress the story. Stage 3 must add read-only game-state recognition and an operator-visible confidence/unknown state before any combat behaviour is considered.


## Follow-up after first real-game Stage 2C acceptance test

Mark ran the four-step example in Ryujinx and confirmed that Rex visibly moved. All eight before/after screenshots were captured, demonstrating that the real-game input path works. The user reported that Ryujinx's window became smaller during automatic activation.

Root cause identified in `WindowsWindowProvider.activate_window`: it called Win32 `ShowWindow(SW_RESTORE)` even when the target was visible but merely not foreground. `SW_RESTORE` can unmaximise a visible maximised window and return it to its previous windowed dimensions.

Fix committed:
- Call `ShowWindow(SW_RESTORE)` only when `IsIconic(hwnd)` reports that the window is actually minimised.
- For visible normal or maximised windows, preserve the current size/state and call `SetForegroundWindow` only.
- Added `tests/test_window_capture.py` with three regression tests: visible normal window unchanged, visible maximised window not unmaximised, and minimised window restored.

The latest confirmed CI result before this follow-up was 45 passing Windows tests. The three new regression tests have been added but their CI result is not yet available. Re-run the full Windows suite and repeat the real-game sequence test to confirm the fix.
