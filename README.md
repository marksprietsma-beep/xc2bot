# XC2 Farming Bot

A Windows-first Python foundation for a future Xenoblade Chronicles 2 farming bot. The intended bot will only repeat a bounded combat loop in an area the player has already reached; it will not progress the story.

## Current status

### Stage 1 — game-independent foundation

- Python package, configuration and CLI.
- Deterministic state machine and mock emulator/controller/screenshot interfaces.
- Safety limits for session duration, consecutive errors, retries and action timing.
- JSONL event log and SQLite session/event store.
- Diagnostic ZIP export with five files; screenshots excluded by default and likely secrets/absolute paths redacted.
- Original 19-test suite passed on the target Windows laptop.

### Stage 2A — read-only Windows observer

- Finds visible windows by title substring and reports process/window metadata.
- Captures the foreground emulator window's visible client area once with MSS.
- Saves PNG screenshots under artifacts/logs/screenshots.
- Records screenshot metadata in JSONL and SQLite.
- Manually accepted against the real Ryujinx XC2 window at 1280 × 760.

### Stage 2B — bounded manual keyboard input

- Loads semantic action-to-key mappings from a local JSON file.
- Sends one Windows virtual-key press through SendInput.
- Automatically restores and activates the uniquely matched Ryujinx game window when the command is launched from PowerShell.
- Checks focus during the hold and sends a matching key-up in a finally cleanup path.
- Requires the explicit --confirm flag.
- Hard-limits each key hold to 0.05–1.00 seconds.
- Polls the physical Ctrl+Alt+F12 emergency-stop chord while the key is held.
- Captures before/after screenshots and records action events in JSONL and SQLite.

Stage 2B is a **manual one-action test interface**, not an unattended bot. It does not repeat actions, identify game states, choose targets, run combat, automate Core Crystal collection, or progress the story. Diagnostic ZIP exports continue to exclude screenshots by default.

### Stage 2C — bounded manual action sequences

- Runs a JSON plan containing 1–8 sequential, single-key actions.
- Every action requires a configured semantic mapping and a 0.05–1.00 second hold.
- Hard limits: 5 seconds total key-hold time, 2 seconds per inter-step delay, 10 seconds total delay, 15 seconds total planned time and 45 seconds maximum measured runtime.
- Requires an explicit --confirm acknowledgement for every sequence.
- Captures before/after screenshots for each action and records per-action child sessions.
- Creates a parent sequence diagnostic session linking successful, failed and interrupted child actions and screenshot filenames.
- Uses the same emergency-stop probe during holds and between actions, including before the first activation.
- Pins the process ID and window handle for the entire sequence; activation is allowed only for the first action. Later focus loss aborts instead of stealing focus back.
- Reads and validates the mapping once; editing its file mid-sequence cannot change the validated keys.
- Missing after screenshots, focus loss, runtime expiry or logging errors cancel all remaining actions. Ctrl+C finalizes interrupted parent/child sessions when diagnostic storage is usable.
- No loops, retries, simultaneous keys, combat decisions, target selection or story progression.

The reviewed changes still require Mark's post-fix Windows acceptance before Stage 3. Only Mark should execute the following real-input acceptance command.

Run the supplied short movement plan from PowerShell:

    .\.venv\Scripts\python.exe -m xc2bot sequence --mapping config\keyboard.json --plan config\sequence.example.json --confirm

The example performs four short directional movements. Test it only in an open, safe area. The sequence is manually initiated and runs once; it is not an unattended farming loop.

## Requirements

- Windows 10/11 (Windows 11 is the target operating system).
- Python 3.12 or newer.
- MSS screen capture library (installed automatically with the project).

## Windows PowerShell setup

    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -e .
    .\.venv\Scripts\python.exe -m xc2bot config
    .\.venv\Scripts\python.exe -m xc2bot test
    .\.venv\Scripts\python.exe -m xc2bot simulate
    .\.venv\Scripts\python.exe -m xc2bot windows --window-title "Ryujinx 1.3.3"
    .\.venv\Scripts\python.exe -m xc2bot observe --window-title "Ryujinx 1.3.3"

## Configure keyboard mappings

Copy the example file and edit the keys to match the keyboard bindings currently configured in Ryujinx:

    Copy-Item config\keyboard.example.json config\keyboard.json
    notepad config\keyboard.json

config/keyboard.json is intentionally git-ignored so local input choices are not committed. The example mirrors the Joy-Con Pair keyboard bindings currently configured on Mark's Ryujinx setup, including WASD movement, IJKL right-stick movement, face buttons, shoulders, triggers and D-pad. PLUS and MINUS are supported as named virtual keys. Check Ryujinx's Input settings before using the example on another machine.

Show the loaded mapping:

    .\.venv\Scripts\python.exe -m xc2bot controls --mapping config\keyboard.json

## One manually confirmed input action

1. Unpause XC2 and place the character somewhere safe.
2. Run exactly one short test from PowerShell. The command finds and activates the configured Ryujinx game window itself; you do not need to click it first:

    .\.venv\Scripts\python.exe -m xc2bot input --mapping config\keyboard.json --action MOVE_FORWARD --duration 0.25 --confirm

The command:
- Captures a before screenshot.
- Sends the mapped key for 0.25 seconds.
- Releases the key.
- Captures an after screenshot if Ryujinx remains foreground.
- Records the action, duration, key, window and screenshot metadata in JSONL and SQLite.

**Safety limits:** one key per command, no modifiers or key combinations, no repeats, maximum hold 1.00 second, foreground-window verification, and an explicit --confirm acknowledgement. Press **Ctrl+Alt+F12** during a hold to request an emergency stop; **Ctrl+C** also interrupts the command. The controller tracks a key before attempting key-down so an interrupt in SendInput still enters key-up cleanup. A failed key-up remains tracked for bounded defensive cleanup and blocks further key-downs. This is best-effort release, not an OS-level guarantee: denied SendInput, process termination or repeated interrupts can prevent cleanup. Stop using the tool if release fails; do not start another input command until the held-key state is resolved.

The command activates the matching Ryujinx window before taking the before screenshot and sending input. If Windows refuses to activate it, the command fails closed and sends no key. If the emulator loses focus during the hold, the action stops and the key is released. Run PowerShell and Ryujinx at the same privilege level; Windows may reject SendInput if their elevation levels differ.

## Commands

- xc2bot config — show effective configuration and non-identifying environment details.
- xc2bot simulate — run a deterministic mock encounter; this does not control a game.
- xc2bot test — run the project's offline unit test suite.
- xc2bot windows --window-title "Ryujinx 1.3.3" — list matching visible windows.
- xc2bot observe --window-title "Ryujinx 1.3.3" — capture the emulator window once; sends no game input.
- xc2bot controls --mapping config\keyboard.json — display configured semantic keyboard mappings.
- xc2bot input ... --confirm — send one bounded mapped key action, with before/after screenshots.
- xc2bot sequence --mapping ... --plan ... --confirm — execute a validated, bounded sequence once, with screenshots and linked diagnostics for every step.
- xc2bot export --output artifacts\diagnostics.zip — export five diagnostic files: summary, JSONL events, configuration, environment information and SQLite database. Screenshots are excluded by default.

Generated logs, databases and screenshots are written under artifacts/logs by default and are git-ignored.

## Observation and input limitations

Screenshots use visible desktop pixels inside the game's Windows client rectangle. The game window must be visible and unobscured; this is not off-screen capture. Keyboard input goes to the current foreground application, so the tool activates the matching Ryujinx window and verifies it immediately before and during a hold. This remains a best-effort desktop automation boundary: it is not a direct emulator-control API.

Safety checks run throughout cooperative holds (20 ms polling) and inter-step waits (50 ms polling), and around action setup/screenshots. Synchronous activation, capture, diagnostic I/O and OS calls cannot be forcibly interrupted by the 45-second runtime guard; expiry cancels further actions when control returns. Desktop focus can change between a check and SendInput/screenshot capture. A valid plan or semantic action name cannot prove that an in-game action is safe; Mark must verify bindings and choose a safe location. Stage 2B's one-shot hold and Stage 2C's manually initiated sequence are bounded; unattended operation is not supported. There is no game-state recognition, combat strategy, target selection, or automatic recovery yet.

See docs/CONTINUATION.md for the continuation checkpoint.

## Public repository notice

Do not commit game files, dumps, emulator firmware, encryption keys, save files, credentials, secrets, personal data or unredacted logs. Use only game and emulator files obtained and used lawfully by the user.

## Stage 2C review validation

Python 3.12.14 cloud validation ran 77 tests: **75 passed, 2 Windows-only tests skipped, 0 failures/errors**. The skipped tests check the native INPUT layout and SendInput structure with a fake API. This is not a Windows or emulator acceptance result. The latest recovery branch also includes the separately committed window-size-preserving activation fix and its three regressions.

New regression coverage includes uncertain/interrupted key-down, failed key-up tracking, blocked subsequent input, mapping-file changes, pinned target identity, first-step stop, mid-hold and inter-step stops, focus loss, deadline expiry after final capture, strict JSON validation and limits, capture/logging failures, ordered releases/captures/delays, CLI validation and interrupted parent/child persistence. All input senders and captures in these tests are fake. No real emulator input was executed during review.

See `docs/CONTINUATION.md` for the audit findings and the outstanding Windows acceptance checklist. Do not begin Stage 3 or merge PR #1 before Mark accepts the reviewed version.
