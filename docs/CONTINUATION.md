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

Stage 2C is implemented. Follow the current review and acceptance checklist below; do not begin Stage 3. Earlier Windows/game observations are historical evidence, not acceptance of the reviewed changes.


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

The follow-up Windows CI run completed successfully: **48 tests passed, 0 failures** on Windows Server 2025 / Python 3.12, including all three new window-activation regression tests. Repeat the real-game sequence test after updating locally to confirm the window remains at its previous size.


## Stage 2C safety review — 2026-10-30

Reviewed branch `recovery/stage-1-foundation`, incorporating the concurrent window-activation fix through `fb44ced2376afcb1fab7d800606e813d6ae7ef31`. Preserved its minimized-only restoration and all three activation tests.

Defects corrected:

- **Untracked injected key:** key-down was outside the cleanup try/finally, and the key was tracked only after SendInput returned. A successful injection followed by Ctrl+C could leave the key held. Track before calling the sender inside the protected region.
- **Forgotten failed release:** cleanup discarded keys even when key-up failed. Retain failed releases, permit bounded defensive key-up cleanup, and reject further key-downs while a release remains unresolved. No action retries were added.
- **Changing inputs after validation:** every child reloaded the mapping. Pass the already validated mapping snapshot to every child.
- **Target change and focus recovery race:** the process was checked only after a child could send input; checking only PID also permitted another window in the same process. Pin PID and HWND before the first child, validate that pair before input and while holding, and permit automatic activation only on the first step.
- **Incomplete stop/deadline coverage:** use one stop probe and clock across the controller and sequence; check before initial activation, during holds, during delays and after the final capture. Keep all original numerical limits unchanged.
- **Incomplete failure diagnostics:** Ctrl+C bypassed exception handlers, and failed children were not linked to their parent. Reserve child IDs in the parent's step-start event, record the parent in child events, log aborted child IDs, and finalize both sessions as interrupted when possible.
- **Continuing without after evidence:** a failed/missing after screenshot now aborts remaining steps and records partial status rather than continuing.
- **Ambiguous configuration:** reject duplicate JSON fields in plans/mappings, overflowing numeric durations/delays and explicit empty title overrides before window/input activity.

Local verification: `.venv` Python **3.12.14**, declared dependency installation (`pip install -e .`) succeeded. `python -m xc2bot test`: **77 run, 75 passed, 2 skipped, 0 failures, 0 errors**. Skips are the existing Windows-only native INPUT-size and fake-SendInput layout tests. No Linux-specific tests or CI were added. Tests use fake input/capture providers and deterministic clocks, including checks that screenshots occur with keys released, actions/delays are ordered, JSONL/SQLite counts agree, and parent/child sessions have matching links and final statuses.

A partial/aborted sequence may already have moved the character. `steps_completed` counts returned child results, not proof that no physical input occurred in the failed child. Inspect its `keyboard_action_*` events, `input_may_have_occurred`, and `keyboard_release_failed` before considering any further manual action. Do not automatically retry.

### Required post-review Windows acceptance (Mark only)

1. Keep PR #1 draft; update the local recovery branch, preserve `config/keyboard.json`, and run `py -3.12 -m venv .venv` only if a venv is absent. Install with `.\.venv\Scripts\python.exe -m pip install -e .` and run `.\.venv\Scripts\python.exe -m xc2bot test`. Expect **90 tests and no skips on Windows** with foreground diagnostics included. Windows CI success validates mocks/native structure layout, not gameplay.
2. Verify mappings against the installed Ryujinx build and use an open, safe in-game area. Perform real input only when Mark explicitly elects to run acceptance. Confirm the initial activation preserves the existing maximized/normal size (minimized windows may be restored).
3. Run the documented example sequence once. Expect four short movements, eight screenshots and four linked child sessions, with no remaining held direction. Inspect parent JSONL/SQLite events and child session statuses.
4. Separately test Ctrl+Alt+F12, Ctrl+C, and switching focus during a hold/inter-step delay. Use a deliberately short, safe plan with a delay so there is time to interrupt. Expect no subsequent key-down, best-effort release, an aborted parent and interrupted/failed child where applicable. No automatic reactivation should follow focus loss. Record the exact step and key state observed.
5. Inspect captures locally for unrelated desktop content. MSS captures visible desktop pixels, with a focus/capture race; exports exclude screenshots by default. The exporter selects the latest child session for its summary/JSONL, while its SQLite copy contains the related parent/child sessions; inspect those links in the local journal/database.
6. Record Python/Windows/Ryujinx/game versions, tested commit, unit-test counts, window-size behavior, stop/focus-loss results and diagnostic IDs. Do not share game files, private screenshots or unredacted paths/secrets. Do not merge or begin Stage 3 until Mark accepts this reviewed version.

Residual risks: Windows can reject key-up; process termination or repeated interrupts can prevent cleanup. Foreground checks cannot make desktop SendInput atomic, nor can they verify that a mapped key is safe in the current game state. Blocking capture/activation/storage calls can overrun the cooperative runtime guard. This remains a bounded manual test engine, not unattended combat/farming or story automation.


## Stage 2C foreground-focus investigation — 2026-10-30

Operator evidence for `sequence-2963331c4938`: W, D, W completed; A was rejected after its before-screenshot and before the final pre-input guard. Initial activation had succeeded. The original verifier enumerates title-matching windows, pins HWND/PID and checks foreground/minimized state; it cannot report the identity of a different foreground application. MSS capture uses previously read window metadata, so capture success does not demonstrate continuing focus. No evidence identifies a particular focus-stealing window or demonstrates a keyboard timing defect. The raw Windows journal for this session was not available in the cloud checkout; these observations are based on the supplied event summary and code inspection.

Added a separate, read-only foreground identity API and `foreground_checkpoint` events in each action's existing child session. Checkpoints bracket both screenshots, precede the existing pre-input guard, follow key release, and report foreground identity on the action failure path after defensive release is attempted. Each records HWND, PID, title, pinned target identity, observation timestamp, capture phase and parent sequence ID. JSONL and SQLite use the existing title/path/secret redaction. An unknown or unavailable foreground is explicit and never substituted for proof of target focus.

No changes to keyboard timing, polling, sequence plans, guards, target selection or activation behavior. The native diagnostic reader performs identity reads only, without calling the shared DPI-initializing helper, activation/restoration, geometry or window-enumeration functions. No telemetry writes were added inside the key-hold polling loop. Diagnostic I/O adds overhead between operations; it is not an atomic trace of desktop focus. Focus loss still aborts; no automatic mid-sequence reactivation was added.

Regression coverage includes the reported three-success/fourth-before-screenshot failure, the other foreground window's identity, unchanged pinned target and one-time activation, checkpoint ordering for both captures/input/release, matching JSONL/SQLite details, title redaction, null foreground, unreadable PID, empty title, focus changing during metadata reads, reader failure, logging failure, stale telemetry failing to bypass the pre-input guard, unchanged hold duration and read-only Win32 calls. All input/capture APIs in tests are fake. Every new test SQLite connection uses `contextlib.closing`.

Local Python 3.12.14 result: **90 run, 88 passed, 2 Windows-only tests skipped, 0 failures/errors**. Run the Windows CI suite for this version, then have Mark inspect these checkpoints on the next explicitly authorized, safe manual Windows test. Compare the fourth child session's `after_screenshot` (`capture_phase=before`), `before_keyboard_input` and `action_failed` foreground identities to locate the observed transition. Do not infer the focus owner from the target title alone. Real-game input was not executed during this investigation. PR #1 must remain draft and unmerged; Stage 3 is not started.
