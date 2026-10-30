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
