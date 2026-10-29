# Codex Task: Stage 1 — XC2 Farming Bot Foundation

You are working in the public GitHub repository `marksprietsma-beep/xc2bot`.

## Project context

Build a Windows desktop automation tool for **Xenoblade Chronicles 2** running locally in a Nintendo Switch emulator. The long-term goal is to let the user take a party to an area they have already reached in the story, enable the bot, and have it repeat a bounded farming loop to collect Core Crystals and hunt Rare Blades.

The bot must **not** progress the story, make story choices, or explore new areas autonomously. The user chooses the location and prepares the party. The bot should be designed for eventual extended unattended farming, with strong safeguards and detailed diagnostics so logs can be reviewed and used to improve the system.

The game and emulator are not yet available for testing. The emulator has not been selected; Yuzu and Ryujinx are both under consideration. Do not assume either one.

Target machine: Windows 11, ASUS ROG Strix SCAR 16 (2024), Intel Core i9-14900HX, NVIDIA RTX 4080 Laptop GPU (12 GB VRAM), 16 GB RAM, 1 TB NVMe SSD. Keep the design proportionate and monitor memory use during future integration.

## Stage 1 objective

Build the game-independent Python foundation. It must run and be testable without the game, emulator, ROM, firmware, encryption keys, or game screenshots. Use a simulated emulator and deterministic test data. Do not claim mock tests prove compatibility with the real game.

## Repository and Git safety

1. Confirm the repository root is `xc2bot` and inspect the existing files and Git state before changing anything.
2. This project is separate from the user's Godot game repository, `The Road Has Gone Dark`. Never modify or push to that repository. If the workspace is not this repository, stop.
3. Create and work on a dedicated branch named `codex/stage-1-foundation` (or a clearly named equivalent if that branch already exists). Do not commit implementation work directly to `main`.
4. Keep changes focused on Stage 1. Do not start emulator-specific integration.
5. This repository is public. Never commit secrets, credentials, personal data, emulator firmware, encryption keys, game files, game dumps, save files, or unredacted logs containing private machine paths. Do not download any of those files.

## Required implementation

### 1. Project structure and setup
- Establish a clear, maintainable Python package layout.
- Document the supported Python version and provide pinned or appropriately constrained dependencies.
- Provide straightforward Windows PowerShell setup and test commands.
- Use configuration files or typed settings for timeouts, retry limits, log location, and safety behaviour. Avoid hard-coded machine-specific paths.
- Add a suitable `.gitignore` for virtual environments, caches, local settings, databases, screenshots, logs, and diagnostic exports.

### 2. Structured diagnostics
- Implement timestamped structured JSONL event logging.
- Use SQLite for durable session/event records and summary statistics, with a small, documented schema.
- Record at least: session ID, event timestamp, event type, current state, requested action, observed result, elapsed time where relevant, retry count, and error details.
- Make logging robust to malformed events and database/write errors. If reliable logging is lost, the bot must pause or stop safely rather than silently continue.
- Add a command to export a diagnostic bundle containing relevant logs, a database summary, effective configuration, application version, Python/Windows environment details, and screenshots if any exist.
- The export must redact or omit sensitive paths and secrets by default. Do not include game assets or unrelated files. Make the bundle easy to inspect and share for debugging.

### 3. Replaceable emulator interfaces
Define clear interfaces/protocols for:
- emulator lifecycle/status (for later use; no real emulator launch required in Stage 1);
- screenshot capture;
- controller input;
- clock/sleep abstraction where useful for deterministic tests.

Provide mock implementations that can simulate inputs, screenshots or observation events and return predictable results. The rest of the application must depend on these interfaces, not directly on a specific emulator or input library.

### 4. Explicit combat state machine
Implement and document a deterministic state machine with at least these states:
- `FIELD`
- `TARGET_DETECTED`
- `COMBAT`
- `VICTORY`
- `DEFEAT`
- `RESET`
- `PAUSED`
- `UNKNOWN`

Define allowed transitions and log each transition. Reject invalid transitions. An ambiguous or unexpected observation must move to `UNKNOWN` or `PAUSED`, not guess. The machine must support a simulated encounter from field state through target detection, combat, victory, and reset back to field.

### 5. Safety controls
- Implement an emergency-stop mechanism that can be tested without the game. If a global hotkey requires an optional platform dependency, isolate it behind an interface and provide a tested keyboard/command fallback.
- Support configurable maximum session duration, action timeout, consecutive-error limit and retry limit.
- Retries must be bounded; no infinite loops.
- Stop or pause on unknown state, repeated failures, loss of logging, or exhausted retry budget.
- The initial implementation must never navigate menus, accept story dialogue, select story choices, or trigger progression.
- Design for a later unattended mode, but do not enable real unattended game control in Stage 1.

### 6. CLI and test simulation
Provide a small command-line interface with commands equivalent to:
- show configuration / environment diagnostics;
- run a deterministic simulated encounter;
- run the automated test suite;
- export a diagnostic bundle.

Use clear exit codes and useful error messages. A simulated run should create a session, emit state/action events, reach a mock victory, reset safely, and produce inspectable diagnostics.

### 7. Automated tests
Use a standard Python test framework. Cover:
- valid and invalid state transitions;
- a complete simulated encounter;
- emergency stop;
- action timeout;
- retry limits and repeated errors;
- unknown-state safe stop;
- logging and SQLite persistence;
- diagnostic export and redaction;
- mock interfaces behaving deterministically.

Tests must run offline and must not require the game or emulator. Do not add Linux CI or Linux-specific validation; Windows 11 is the target. If CI is added, keep it Windows-only and proportionate.

## Engineering standards
- Prefer a small, understandable architecture over speculative abstraction.
- Keep the real-time control path deterministic; do not add LLM/API calls to it.
- Use type hints and concise docstrings where they improve clarity.
- Handle exceptions deliberately. Do not swallow errors silently.
- Avoid unnecessary dependencies and heavyweight computer-vision libraries in Stage 1.
- Do not implement real image recognition, enemy detection, target selection, combat timing, farming optimisation, or emulator-specific controller automation until real-game integration can be tested.
- Do not assume that a simulated success means real-game compatibility.

## Acceptance criteria

Stage 1 is complete only when:
1. A fresh Windows 11 environment can follow the README and install the project.
2. The documented test command runs successfully without the game or emulator.
3. Tests cover the safety and state-machine cases listed above.
4. A simulated encounter completes and produces structured, inspectable logs and SQLite records.
5. A diagnostic bundle can be generated and its contents inspected; sensitive data is excluded or redacted by default.
6. The working tree is clean after committing the completed changes on the dedicated branch.
7. Documentation states clearly what is mocked, what is tested, and what remains unverified.

## Completion report and checkpoint

When finished, report:
- the branch and commit SHA;
- the main files/modules created;
- exact setup, simulation, test and export commands;
- the test results, including counts and any failures;
- an example of the generated diagnostic output and bundle contents;
- known limitations and any decisions that need the user's input;
- the next smallest recommended stage.

Update the README and add a continuation checkpoint such as `docs/CONTINUATION.md`. Stop after Stage 1. Do not start real emulator integration until the user supplies the installed emulator build, game version, and a working in-game test setup.
