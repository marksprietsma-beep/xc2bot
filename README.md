# XC2 Farming Bot

A Windows-first Python foundation for a future Xenoblade Chronicles 2 farming bot. The intended bot will only repeat a bounded combat loop in an area the player has already reached; it will not progress the story.

## Current status

Stage 1's game-independent foundation is implemented and has passed its 19-test suite on the target Windows laptop.

Stage 2A adds a **read-only, one-shot Windows game-window observer**:
- Finds visible windows whose title contains Xenoblade Chronicles 2.
- Can list matching windows with the xc2bot windows command.
- Brings the unique game window to the foreground and captures its client area once with MSS.
- Saves a PNG under artifacts/logs/screenshots and records a screenshot_captured event in JSONL and SQLite.

Stage 2A sends **no keyboard/controller input**, performs no screen recognition, and does not automate gameplay. The screenshot is deliberately stored separately; diagnostic ZIP exports continue to exclude image files by default.

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
    .\.venv\Scripts\python.exe -m xc2bot windows
    .\.venv\Scripts\python.exe -m xc2bot observe
    .\.venv\Scripts\python.exe -m xc2bot export --output artifacts\diagnostics.zip

Configuration can be loaded from config/default.json by passing --config config/default.json to supported commands. Generated logs and databases are written to artifacts/logs by default and are git-ignored.

## Commands

- xc2bot config — show effective configuration and non-identifying environment details.
- xc2bot simulate — run a deterministic mock encounter; this does not control a game.
- xc2bot test — run the project's offline unit test suite.
- xc2bot windows — list visible windows whose title contains the XC2 title.
- xc2bot observe — capture the game window once, bring it to the foreground, save a PNG and record a diagnostic event. Use --no-activate to require that it is already active.
- xc2bot export --output artifacts\diagnostics.zip — export five diagnostic files: summary, JSONL events, configuration, environment information and SQLite database. Screenshots are excluded by default.

## Observation details and limitations

The observer captures the visible desktop pixels inside the game's Windows client rectangle. It activates the game window first and fails closed if it cannot make that window foreground. Keep the game window visible and unobscured; this is not an off-screen capture API and cannot reliably capture a window covered by another application. The capture is a manual, one-shot operation; there is no polling loop.

The default title match is Xenoblade Chronicles 2. If the game window uses a different title, pass --window-title "text to match". The PNG is saved locally and its filename, dimensions, title and process ID are logged. The diagnostic ZIP does not include the PNG; attach it separately when sharing a visual example.

## Safety and limitations

The state machine fails closed on unknown observations. Retries are bounded. The safety wrapper detects a synchronous action that returns after its timeout, but cannot interrupt a backend call that remains blocked; future real input integration must isolate blocking operations in a controllable worker or process before unattended operation is considered safe. Stage 2A does not send inputs, recognise game states, or provide a global Windows emergency hotkey.

See docs/CONTINUATION.md for the next steps and remaining limitations.

## Public repository notice

Do not commit game files, dumps, emulator firmware, encryption keys, save files, credentials, secrets, personal data or unredacted logs. Use only game and emulator files obtained and used lawfully by the user.
