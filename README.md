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
- Requires the Ryujinx game window to already be foreground; it never steals focus.
- Checks focus during the hold and sends a matching key-up in a finally cleanup path.
- Requires the explicit --confirm flag.
- Hard-limits each key hold to 0.05–1.00 seconds.
- Polls the physical Ctrl+Alt+F12 emergency-stop chord while the key is held.
- Captures before/after screenshots and records action events in JSONL and SQLite.

Stage 2B is a **manual one-action test interface**, not an unattended bot. It does not repeat actions, identify game states, choose targets, run combat, automate Core Crystal collection, or progress the story. Diagnostic ZIP exports continue to exclude screenshots by default.

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

config/keyboard.json is intentionally git-ignored so local input choices are not committed. The example uses WASD, Enter and Escape as illustrative values only; check Ryujinx's Input settings rather than assuming these match your setup.

Show the loaded mapping:

    .\.venv\Scripts\python.exe -m xc2bot controls --mapping config\keyboard.json

## One manually confirmed input action

1. Unpause XC2 and place the character somewhere safe.
2. Click the actual Ryujinx game window so it is the foreground window.
3. Run exactly one short test:

    .\.venv\Scripts\python.exe -m xc2bot input --mapping config\keyboard.json --action MOVE_FORWARD --duration 0.25 --confirm

The command:
- Captures a before screenshot.
- Sends the mapped key for 0.25 seconds.
- Releases the key.
- Captures an after screenshot if Ryujinx remains foreground.
- Records the action, duration, key, window and screenshot metadata in JSONL and SQLite.

**Safety limits:** one key per command, no modifiers or key combinations, no repeats, maximum hold 1.00 second, foreground-window verification, and an explicit --confirm acknowledgement. Press **Ctrl+Alt+F12** during a hold to request an emergency stop; **Ctrl+C** also interrupts the command. The controller attempts to release any key it has pressed on every exit path.

If the emulator loses focus, the action stops and the key is released. The command does not activate or restore the emulator automatically. Run PowerShell and Ryujinx at the same privilege level; Windows may reject SendInput if their elevation levels differ.

## Commands

- xc2bot config — show effective configuration and non-identifying environment details.
- xc2bot simulate — run a deterministic mock encounter; this does not control a game.
- xc2bot test — run the project's offline unit test suite.
- xc2bot windows --window-title "Ryujinx 1.3.3" — list matching visible windows.
- xc2bot observe --window-title "Ryujinx 1.3.3" — capture the emulator window once; sends no game input.
- xc2bot controls --mapping config\keyboard.json — display configured semantic keyboard mappings.
- xc2bot input ... --confirm — send one bounded mapped key action, with before/after screenshots.
- xc2bot export --output artifacts\diagnostics.zip — export five diagnostic files: summary, JSONL events, configuration, environment information and SQLite database. Screenshots are excluded by default.

Generated logs, databases and screenshots are written under artifacts/logs by default and are git-ignored.

## Observation and input limitations

Screenshots use visible desktop pixels inside the game's Windows client rectangle. The game window must be visible and unobscured; this is not off-screen capture. Keyboard input goes to the current foreground application, so the tool verifies the matching Ryujinx window immediately before and during a hold. This remains a best-effort desktop automation boundary: it is not a direct emulator-control API.

Synchronous timeouts are detected only after a blocking action returns. Stage 2B's short one-shot hold is bounded, but unattended operation is not supported. There is no game-state recognition, combat strategy, target selection, or automatic recovery yet.

See docs/CONTINUATION.md for the continuation checkpoint.

## Public repository notice

Do not commit game files, dumps, emulator firmware, encryption keys, save files, credentials, secrets, personal data or unredacted logs. Use only game and emulator files obtained and used lawfully by the user.
