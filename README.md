# XC2 Farming Bot

A Windows-first Python foundation for a future Xenoblade Chronicles 2 farming bot. The intended bot will only repeat a bounded combat loop in an area the player has already reached; it will not progress the story.

## Current status

Stage 1 foundation is implemented on this branch. Emulator, controller and screenshot interfaces are mocks only. No real game control, screen recognition or farming strategy is implemented. Passing mock tests does not establish real-game compatibility.

## Requirements

- Windows 11 is the target operating system.
- Python 3.12 or newer.
- No third-party runtime dependencies are required for the Stage 1 foundation.

## Windows PowerShell setup

    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -e .
    .\.venv\Scripts\python.exe -m xc2bot config
    .\.venv\Scripts\python.exe -m xc2bot test
    .\.venv\Scripts\python.exe -m xc2bot simulate
    .\.venv\Scripts\python.exe -m xc2bot export --output artifacts\diagnostics.zip

Configuration can be loaded from config/default.json by passing --config config/default.json to supported commands. Generated logs and databases are written to artifacts/logs by default and are git-ignored.

## Commands

- xc2bot config — show effective settings and non-identifying environment details.
- xc2bot simulate — run a deterministic mock encounter; this does not control a game.
- xc2bot test — run the offline unit test suite.
- xc2bot export --output artifacts\diagnostics.zip — export five files: summary, JSONL events, configuration, environment information and SQLite database. Screenshots are excluded by default.

## Safety and limitations

The state machine fails closed on unknown observations. Retries are bounded. The safety wrapper detects a synchronous action that returns after its timeout, but cannot interrupt a backend call that remains blocked; real emulator integration must isolate blocking operations in a controllable worker or process before unattended operation is considered safe. Stage 1 does not implement a global OS-level hotkey or real emulator integration.

See docs/CONTINUATION.md for the next steps and remaining limitations.

## Public repository notice

Do not commit game files, dumps, emulator firmware, encryption keys, save files, credentials, secrets, personal data or unredacted logs. Use only game and emulator files obtained and used lawfully by the user.
