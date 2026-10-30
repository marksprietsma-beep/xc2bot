"""Configuration with conservative defaults and validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Settings:
    log_dir: str = "artifacts/logs"
    max_session_seconds: float = 3600.0
    action_timeout_seconds: float = 10.0
    max_consecutive_errors: int = 3
    max_retries: int = 2
    include_screenshots_in_export: bool = False

    def __post_init__(self) -> None:
        if not self.log_dir.strip():
            raise ValueError("log_dir must not be empty")
        if self.max_session_seconds <= 0:
            raise ValueError("max_session_seconds must be positive")
        if self.action_timeout_seconds <= 0:
            raise ValueError("action_timeout_seconds must be positive")
        if self.max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be at least 1")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from JSON, or return safe defaults if no path is supplied."""
    if path is None:
        return Settings()
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a JSON object")
    allowed = set(Settings.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    return Settings(**raw)
