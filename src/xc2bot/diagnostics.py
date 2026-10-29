"""Structured JSONL/SQLite diagnostics and conservative ZIP export."""

from __future__ import annotations

import json
import platform
from contextlib import contextmanager
import re
import sqlite3
import sys
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .config import Settings

_SECRET_KEY = re.compile(r"(token|password|secret|api[_-]?key|authorization|cookie)", re.IGNORECASE)
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\?)*")
_UNIX_PATH = re.compile(r"(?<!\w)/(?:home|Users|workspace|private|tmp|mnt)/[^\s\"']+")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


@contextmanager
def _connect(database: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection that commits/rolls back and always closes.

    sqlite3.Connection's context manager handles transactions but does not
    close the connection, which can prevent temporary database files being
    removed on Windows.
    """
    connection = sqlite3.connect(database)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def redact(value: Any, *, key: str = "") -> Any:
    """Redact likely secrets and machine-specific paths recursively."""
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _WINDOWS_PATH.sub("<PATH>", value)
        value = _UNIX_PATH.sub("<PATH>", value)
        return value
    return value


class DiagnosticError(RuntimeError):
    """Raised when diagnostics cannot be written reliably."""


class DiagnosticStore:
    def __init__(self, root: str | Path, settings: Settings, *, session_id: str | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.session_id = session_id or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.events_path = self.root / "events.jsonl"
        self.database_path = self.root / "sessions.sqlite3"
        self._lock = threading.RLock()
        try:
            with _connect(self.database_path) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    ended_at TEXT, final_state TEXT, status TEXT NOT NULL,
                    config_json TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL, state TEXT, action TEXT,
                    result TEXT, elapsed_seconds REAL, retry_count INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
                db.execute(
                    "INSERT OR IGNORE INTO sessions(session_id, started_at, status, config_json) VALUES (?, ?, ?, ?)",
                    (self.session_id, utc_now(), "running", json.dumps(redact(settings.as_dict()), sort_keys=True)),
                )
        except sqlite3.Error as exc:
            raise DiagnosticError("Unable to initialise diagnostics database") from exc

    def log_event(
        self,
        event_type: str,
        *,
        state: str | None = None,
        action: str | None = None,
        result: str | None = None,
        elapsed_seconds: float | None = None,
        retry_count: int = 0,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = redact({
            "session_id": self.session_id,
            "timestamp": utc_now(),
            "event_type": event_type,
            "state": state,
            "action": action,
            "result": result,
            "elapsed_seconds": elapsed_seconds,
            "retry_count": retry_count,
            "details": details or {},
        })
        with self._lock:
            try:
                with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                with _connect(self.database_path) as db:
                    db.execute(
                        """INSERT INTO events(session_id, timestamp, event_type, state, action, result,
                           elapsed_seconds, retry_count, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (event["session_id"], event["timestamp"], event_type, state, action, result,
                         elapsed_seconds, retry_count, json.dumps(event["details"], ensure_ascii=False, sort_keys=True)),
                    )
            except (OSError, sqlite3.Error) as exc:
                raise DiagnosticError("Unable to persist diagnostic event; stopping is required") from exc
        return event

    def finish_session(self, *, final_state: str, status: str = "completed") -> None:
        with self._lock, sqlite3.connect(self.database_path) as db:
            db.execute(
                "UPDATE sessions SET ended_at=?, final_state=?, status=? WHERE session_id=?",
                (utc_now(), final_state, status, self.session_id),
            )

    def export_bundle(self, output: str | Path) -> Path:
        """Export five safe files. Screenshots are excluded by default."""
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                with _connect(self.database_path) as db:
                    db.commit()
                    session_row = db.execute(
                        "SELECT session_id, started_at, ended_at, final_state, status FROM sessions WHERE session_id=?",
                        (self.session_id,),
                    ).fetchone()
                    event_count = db.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (self.session_id,)).fetchone()[0]
                    victories = db.execute(
                        "SELECT COUNT(*) FROM events WHERE session_id=? AND event_type='state_transition' AND json_extract(details_json, '$.to')='VICTORY'",
                        (self.session_id,),
                    ).fetchone()[0]
                    db.execute("PRAGMA wal_checkpoint(FULL)")
                    with tempfile.TemporaryDirectory(prefix="xc2bot-export-") as temp_dir:
                        temp = Path(temp_dir)
                        summary = {
                            "application_version": __version__,
                            "session": {
                                "session_id": session_row[0], "started_at": session_row[1],
                                "ended_at": session_row[2], "final_state": session_row[3], "status": session_row[4],
                            } if session_row else None,
                            "sessions_in_database": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                            "events_in_session": event_count,
                            "victories_in_session": victories,
                            "screenshots_included": False,
                        }
                        (temp / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                        config_data = redact(self.settings.as_dict())
                        (temp / "config.json").write_text(json.dumps(config_data, indent=2), encoding="utf-8")
                        environment = {
                            "python_version": sys.version.split()[0],
                            "implementation": platform.python_implementation(),
                            "os": platform.system(),
                            "os_release": platform.release(),
                            "architecture": platform.machine(),
                            "application_version": __version__,
                        }
                        (temp / "environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
                        safe_events = []
                        if self.events_path.exists():
                            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                                if not line.strip():
                                    continue
                                try:
                                    item = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                if item.get("session_id") == self.session_id:
                                    safe_events.append(json.dumps(redact(item), ensure_ascii=False, sort_keys=True))
                        (temp / "events.jsonl").write_text("\n".join(safe_events) + ("\n" if safe_events else ""), encoding="utf-8")
                        safe_db = temp / "sessions.sqlite3"
                        with _connect(safe_db) as destination:
                            db.backup(destination)
                        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                            for filename in ("summary.json", "events.jsonl", "config.json", "environment.json", "sessions.sqlite3"):
                                archive.write(temp / filename, arcname=filename)
            except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
                raise DiagnosticError("Unable to export diagnostic bundle") from exc
        return output_path


def environment_info() -> dict[str, str]:
    """Return non-identifying environment details; never returns usernames or paths."""
    return {
        "python_version": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "application_version": __version__,
    }
