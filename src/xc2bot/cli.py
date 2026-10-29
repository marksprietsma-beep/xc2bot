"""Command-line interface for diagnostics and mock-only validation."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

from . import __version__
from .config import Settings, load_settings
from .diagnostics import DiagnosticStore, environment_info
from .simulator import run_simulation


def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.config) if getattr(args, "config", None) else Settings()
    if getattr(args, "log_dir", None):
        values = settings.as_dict()
        values["log_dir"] = args.log_dir
        settings = Settings(**values)
    return settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xc2bot", description="XC2 farming bot foundation (mock mode only)")
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
        if args.command == "export":
            settings = _settings(args)
            log_dir = Path(settings.log_dir)
            events = log_dir / "events.jsonl"
            db_path = log_dir / "sessions.sqlite3"
            if not events.exists() or not db_path.exists():
                print("No diagnostic session found. Run 'xc2bot simulate' first.", file=sys.stderr)
                return 2
            import sqlite3
            with sqlite3.connect(db_path) as db:
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
