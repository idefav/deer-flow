"""Minimal bootstrap configuration for selecting the runtime config source.

DB-backed config cannot be loaded from the DB until the process already knows
which DB to connect to. This module deliberately reads only environment
variables and does not import AppConfig.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from deerflow.config.database_config import DatabaseConfig

ConfigSourceMode = Literal["file", "db"]

_CONFIG_SOURCE_ENV = "DEER_FLOW_CONFIG_SOURCE"
_DATABASE_URL_ENV = "DEER_FLOW_DATABASE_URL"


def get_config_source_mode() -> ConfigSourceMode:
    """Return the configured runtime config source mode.

    Defaults to ``file`` so existing deployments keep their current behavior.
    """
    value = os.getenv(_CONFIG_SOURCE_ENV, "file").strip().lower()
    if value in ("", "file"):
        return "file"
    if value == "db":
        return "db"
    raise ValueError(f"{_CONFIG_SOURCE_ENV} must be 'file' or 'db', got {value!r}")


def is_db_config_enabled() -> bool:
    """Return True when Harness should load runtime config from the DB."""
    return get_config_source_mode() == "db"


def get_bootstrap_database_url() -> str | None:
    """Return the bootstrap DB URL used before DB-backed AppConfig exists."""
    value = os.getenv(_DATABASE_URL_ENV)
    if value is None:
        return None
    value = value.strip()
    return value or None


def database_config_from_url(url: str) -> DatabaseConfig:
    """Return a DatabaseConfig derived from a sqlite or postgresql SQLAlchemy URL."""
    if url.startswith("sqlite+aiosqlite:///"):
        sqlite_path = Path(url.removeprefix("sqlite+aiosqlite:///"))
        return DatabaseConfig(backend="sqlite", sqlite_dir=str(sqlite_path.parent))
    if url.startswith("sqlite:///"):
        sqlite_path = Path(url.removeprefix("sqlite:///"))
        return DatabaseConfig(backend="sqlite", sqlite_dir=str(sqlite_path.parent))
    if url.startswith("postgresql://") or url.startswith("postgresql+asyncpg://"):
        return DatabaseConfig(backend="postgres", postgres_url=url)
    raise ValueError(f"{_DATABASE_URL_ENV} must be a sqlite or postgresql SQLAlchemy URL, got {url!r}")


def get_bootstrap_database_config() -> DatabaseConfig | None:
    """Return a DatabaseConfig derived from the bootstrap DB URL, if present."""
    url = get_bootstrap_database_url()
    if url is None:
        return None
    return database_config_from_url(url)
