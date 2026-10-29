# XC2 Farming Bot

A Windows-first, Python-based automation project for repeatable, user-selected farming sessions in Xenoblade Chronicles 2. The intended bot will eventually automate a bounded combat loop in an area the player has already reached; it must not progress the story.

## Current status

Pre-development. The game and emulator are not yet available for integration testing. Yuzu and Ryujinx are both under consideration.

## Start here

- [Codex Stage 1 prompt](CODEX_PROMPT_STAGE_1.md) — self-contained instructions for building the game-independent foundation.
- Stage 1 is limited to project scaffolding, structured diagnostics, mock emulator interfaces, a safe combat state machine, CLI simulation and automated tests. It must not implement unverified real-game automation.

## Target environment

Windows 11. Development target: ASUS ROG Strix SCAR 16 (2024), Intel Core i9-14900HX, NVIDIA RTX 4080 Laptop GPU (12 GB VRAM), 16 GB RAM.

## Public repository notice

This repository is public. Do not commit game files, game dumps, emulator firmware, encryption keys, save files, credentials, secrets, personal data, or unredacted diagnostic logs. Use only game and emulator files obtained and used lawfully by the user.
