"""Configuration and filesystem locations used by LuluJar.

Everything lives under a single home directory so that a run is easy to clean
up:  ``~/.lulujar`` by default, overridable with ``$LULUJAR_HOME``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

APP_NAME = "lulujar"


def home_dir() -> Path:
    """Root directory holding uploads, extracted output, logs and settings."""
    env = os.environ.get("LULUJAR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".lulujar"


def uploads_dir() -> Path:
    return home_dir() / "uploads"


def output_dir() -> Path:
    return home_dir() / "extracted"


def cache_dir() -> Path:
    return home_dir() / "cache"


def bin_dir() -> Path:
    """Where auto-installed helper binaries (e.g. 7-Zip) are stored."""
    return home_dir() / "bin"


def config_path() -> Path:
    return home_dir() / "config.json"


@dataclass
class Settings:
    """User editable settings, persisted as JSON in the home directory."""

    #: Roots a browser session may browse.  The destination picker and the
    #: "extract a file already on this machine" flow are confined to these.
    browse_roots: List[str] = field(default_factory=list)

    #: Default destination for new extractions.
    default_destination: Optional[str] = None

    #: Hard cap for a single upload, in bytes.  ``0`` means unlimited.
    max_upload_bytes: int = 0

    #: Number of bytes sent per upload chunk by the browser client.
    chunk_size: int = 8 * 1024 * 1024

    def to_dict(self) -> dict:
        return {
            "browse_roots": list(self.browse_roots),
            "default_destination": self.default_destination,
            "max_upload_bytes": self.max_upload_bytes,
            "chunk_size": self.chunk_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        settings = cls()
        if isinstance(data, dict):
            roots = data.get("browse_roots")
            if isinstance(roots, list):
                settings.browse_roots = [str(r) for r in roots]
            if data.get("default_destination"):
                settings.default_destination = str(data["default_destination"])
            if isinstance(data.get("max_upload_bytes"), int):
                settings.max_upload_bytes = data["max_upload_bytes"]
            if isinstance(data.get("chunk_size"), int) and data["chunk_size"] > 0:
                settings.chunk_size = data["chunk_size"]
        return settings


def load_settings() -> Settings:
    path = config_path()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                return Settings.from_dict(json.load(handle))
        except (OSError, ValueError):
            pass
    return Settings()


def save_settings(settings: Settings) -> None:
    home_dir().mkdir(parents=True, exist_ok=True)
    with config_path().open("w", encoding="utf-8") as handle:
        json.dump(settings.to_dict(), handle, indent=2)


def ensure_dirs() -> None:
    for directory in (home_dir(), uploads_dir(), output_dir(), cache_dir()):
        directory.mkdir(parents=True, exist_ok=True)
