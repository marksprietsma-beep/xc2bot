"""Command-line interface for diagnostics, observation and mock validation."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from contextlib import closing
from pathlib import Path

from . import __version__
from .config import Settings, load_settings
from .diagnostics import DiagnosticStore, environment_info
from .observer import DEFAULT_GAME_TITLE, find_game_windows, observe_once
from .simulator import run_simulation


def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.config) if getattr(args, "config", None) else Settings()
    if getattr(args, "log_dir", None):
        values = settings.as_dict()
        values["log_dir"] = args.log_dir
        settings = Settings(**values)
    return settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xc2bot",
        description="XC2 farming bot foundation: read-only Windows observer and offline mock tools",
    )
    parser.add_argument("--version", action="version", version=f"xc2bot {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    config_parser = sub.add_parser("config", help="show effective configuration and non-identifying environment")
    config_parser.add_argument("--config", help="optional JSON configuration file")

    sim = sub.add_parser("simulate", help="run a deterministic mock encounter; does not control a game")
    sim.add_argument("--config", help="optional JSON configuration file")
    sim.add_argument("--log-dir", help="override log directory")

    test = sub.add_parser("test", help="run the project's unittest suite")
    test.add_argument("--start-directory", default="tests")

    export = sub.add_parser("export", help="export a safe diagnostic ZIP for the latest session")
    export.add_argument("--output", required=True, help="output ZIP path")
    export.add_argument("--config", help="optional JSON configuration file")
    export.add_argument("--log-dir", help="override log directory")

    windows = sub.add_parser("windows", help="list visible windows whose title matches XC2")
    windows.add_argument("--window-title", default=DEFAULT_GAME_TITLE, help="case-insensitive title substring")

    observe = sub.add_parser("observe", help="capture the XC2 game window once; sends no game input")
    observe.add_argument("--window-title", default=DEFAULT_GAME_TITLE, help="case-insensitive title substring")
    observe.add_argument("--output", help="optional PNG output path; defaults to artifacts/logs/screenshots")
    observe.add_argument("--log-dir", help="override diagnostic log directory")
    observe.add_argument(
        "--no-activate",
        action="store_true",
        help="do not bring the game window to the foreground; capture only if it is already active",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "config":
            settings = _settings(args)
            print(json.dumps({"version": __version__, "config": settings.as_dict(), "environment": environment_info()}, indent=2))
            return 0
        if args.command == "simulate":
            settings = _settings(args)
            result = run_simulation(settings, log_dir=settings.log_dir)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "test":
            suite = unittest.defaultTestLoader.discover(args.start_directory)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
        if args.command == "windows":
            matches = find_game_windows(args.window_title)
            payload = [
                {
                    "title": item.title,
                    "process_id": item.process_id,
                    "window_handle": item.handle,
                    "client_area": {
                        "left": item.left,
                        "top": item.top,
                        "width": item.width,
                        "height": item.height,
                    },
                    "is_foreground": item.is_foreground,
                    "is_minimized": item.is_minimized,
                }
                for item in matches
            ]
            print(json.dumps(payload, indent=2))
            return 0 if matches else 1
        if args.command == "observe":
            settings = _settings(args)
            result = observe_once(
                settings,
                title_contains=args.window_title,
                output_path=args.output,
                activate=not args.no_activate,
            )
            print(json.dumps(result.as_dict(), indent=2))
            return 0
        if args.command == "export":
            settings = _settings(args)
            log_dir = Path(settings.log_dir)
            events = log_dir / "events.jsonl"
            db_path = log_dir / "sessions.sqlite3"
            if not events.exists() or not db_path.exists():
                print("No diagnostic session found. Run 'xc2bot simulate' or 'xc2bot observe' first.", file=sys.stderr)
                return 2
            import sqlite3
            with closing(sqlite3.connect(db_path)) as db:
                row = db.execute("SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
            if row is None:
                print("No diagnostic session found in database.", file=sys.stderr)
                return 2
            store = DiagnosticStore(log_dir, settings, session_id=row[0])
            output = store.export_bundle(args.output)
            print(json.dumps({"output": str(output), "bytes": output.stat().st_size, "session_id": row[0]}, indent=2))
            return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
