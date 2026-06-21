from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text


def sandbox_advisory_lock_key(thread_id: str, sandbox_id: str) -> int:
    digest = hashlib.sha256(f"deerflow:sandbox:{thread_id}:{sandbox_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def _sync_postgres_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@dataclass
class PostgresAdvisorySandboxLock:
    postgres_url: str
    lock_key: int
    _engine: Any | None = None
    _connection: Any | None = None

    def __enter__(self) -> PostgresAdvisorySandboxLock:
        self._engine = create_engine(_sync_postgres_url(self.postgres_url), pool_pre_ping=True)
        self._connection = self._engine.connect()
        self._connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": self.lock_key})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._connection is not None:
                self._connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": self.lock_key})
                self._connection.close()
        finally:
            if self._engine is not None:
                self._engine.dispose()


def make_sandbox_creation_lock(app_config, thread_id: str, sandbox_id: str) -> PostgresAdvisorySandboxLock:
    if app_config.runtime_storage.backend != "object":
        raise RuntimeError("Sandbox advisory lock is only used for runtime_storage.backend=object")
    if app_config.database.backend != "postgres":
        raise RuntimeError("runtime_storage.backend=object requires database.backend=postgres for sandbox creation advisory locks")
    return PostgresAdvisorySandboxLock(
        postgres_url=app_config.database.postgres_url,
        lock_key=sandbox_advisory_lock_key(thread_id, sandbox_id),
    )
