"""Memory storage providers."""

import abc
import copy
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from deerflow.config.agents_config import AGENT_NAME_PATTERN
from deerflow.config.app_config import get_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.memory_config import get_memory_config
from deerflow.config.paths import get_paths
from deerflow.persistence.base import Base
from deerflow.persistence.memory.model import MemoryRow

logger = logging.getLogger(__name__)

_FILE_MEMORY_STORAGE_CLASS = "deerflow.agents.memory.storage.FileMemoryStorage"
_DB_MEMORY_STORAGE_CLASS = "deerflow.agents.memory.storage.DbMemoryStorage"


def utc_now_iso_z() -> str:
    """Current UTC time as ISO-8601 with ``Z`` suffix (matches prior naive-UTC output)."""
    return datetime.now(UTC).isoformat().removesuffix("+00:00") + "Z"


def create_empty_memory() -> dict[str, Any]:
    """Create an empty memory structure."""
    return {
        "version": "1.0",
        "lastUpdated": utc_now_iso_z(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


class MemoryStorage(abc.ABC):
    """Abstract base class for memory storage providers."""

    @abc.abstractmethod
    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Load memory data for the given agent."""
        pass

    @abc.abstractmethod
    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Force reload memory data for the given agent."""
        pass

    @abc.abstractmethod
    def save(self, memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
        """Save memory data for the given agent."""
        pass


class FileMemoryStorage(MemoryStorage):
    """File-based memory storage provider."""

    def __init__(self):
        """Initialize the file memory storage."""
        # Per-user/agent memory cache: keyed by (user_id, agent_name) tuple (None = global)
        # Value: (memory_data, file_mtime)
        self._memory_cache: dict[tuple[str | None, str | None], tuple[dict[str, Any], float | None]] = {}
        # Guards all reads and writes to _memory_cache across concurrent callers.
        self._cache_lock = threading.Lock()

    def _validate_agent_name(self, agent_name: str) -> None:
        """Validate that the agent name is safe to use in filesystem paths.

        Uses the repository's established AGENT_NAME_PATTERN to ensure consistency
        across the codebase and prevent path traversal or other problematic characters.
        """
        if not agent_name:
            raise ValueError("Agent name must be a non-empty string.")
        if not AGENT_NAME_PATTERN.match(agent_name):
            raise ValueError(f"Invalid agent name {agent_name!r}: names must match {AGENT_NAME_PATTERN.pattern}")

    def _get_memory_file_path(self, agent_name: str | None = None, *, user_id: str | None = None) -> Path:
        """Get the path to the memory file."""
        if user_id is not None:
            if agent_name is not None:
                self._validate_agent_name(agent_name)
                return get_paths().user_agent_memory_file(user_id, agent_name)
            config = get_memory_config()
            if config.storage_path and Path(config.storage_path).is_absolute():
                return Path(config.storage_path)
            return get_paths().user_memory_file(user_id)
        # Legacy: no user_id
        if agent_name is not None:
            self._validate_agent_name(agent_name)
            return get_paths().agent_memory_file(agent_name)
        config = get_memory_config()
        if config.storage_path:
            p = Path(config.storage_path)
            return p if p.is_absolute() else get_paths().base_dir / p
        return get_paths().memory_file

    def _load_memory_from_file(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Load memory data from file."""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)

        if not file_path.exists():
            return create_empty_memory()

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file: %s", e)
            return create_empty_memory()

    @staticmethod
    def _cache_key(agent_name: str | None = None, *, user_id: str | None = None) -> tuple[str | None, str | None]:
        return (user_id, agent_name)

    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Load memory data (cached with file modification time check)."""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            current_mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            current_mtime = None

        with self._cache_lock:
            cached = self._memory_cache.get(cache_key)
            if cached is not None and cached[1] == current_mtime:
                return cached[0]

        memory_data = self._load_memory_from_file(agent_name, user_id=user_id)

        with self._cache_lock:
            self._memory_cache[cache_key] = (memory_data, current_mtime)

        return memory_data

    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Reload memory data from file, forcing cache invalidation."""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        memory_data = self._load_memory_from_file(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            mtime = None

        with self._cache_lock:
            self._memory_cache[cache_key] = (memory_data, mtime)
        return memory_data

    def save(self, memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
        """Save memory data to file and update cache."""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            # Shallow-copy before adding lastUpdated so the caller's dict is not
            # mutated as a side-effect, and the cache reference is not silently
            # updated before the file write succeeds.
            memory_data = {**memory_data, "lastUpdated": utc_now_iso_z()}

            temp_path = file_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(memory_data, f, indent=2, ensure_ascii=False)

            temp_path.replace(file_path)

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = None

            with self._cache_lock:
                self._memory_cache[cache_key] = (memory_data, mtime)
            logger.info("Memory saved to %s", file_path)
            return True
        except OSError as e:
            logger.error("Failed to save memory file: %s", e)
            return False


class DbMemoryStorage(MemoryStorage):
    """DB-backed memory storage provider using the existing memory JSON shape."""

    def __init__(self, *, database_config: DatabaseConfig | None = None):
        self._database_config = database_config or get_app_config().database
        self._engine = create_engine(self._sync_sqlalchemy_url(self._database_config))
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)
        self._memory_cache: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
        self._cache_lock = threading.Lock()

    @staticmethod
    def _sync_sqlalchemy_url(config: DatabaseConfig) -> str:
        if config.backend == "sqlite":
            Path(config.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{config.sqlite_path}"
        if config.backend == "postgres":
            url = config.postgres_url
            if url.startswith("postgresql+asyncpg://"):
                url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
            elif url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            return url
        raise ValueError("DbMemoryStorage requires sqlite or postgres database backend")

    def _validate_agent_name(self, agent_name: str) -> None:
        if not agent_name:
            raise ValueError("Agent name must be a non-empty string.")
        if not AGENT_NAME_PATTERN.match(agent_name):
            raise ValueError(f"Invalid agent name {agent_name!r}: names must match {AGENT_NAME_PATTERN.pattern}")

    def _scope(self, agent_name: str | None = None, *, user_id: str | None = None) -> tuple[str, str]:
        if agent_name is not None:
            self._validate_agent_name(agent_name)
        return (user_id or "", agent_name or "")

    def _load_rows(self, session: Session, owner_user_id: str, agent_scope: str) -> list[MemoryRow]:
        stmt = select(MemoryRow).where(
            MemoryRow.owner_user_id == owner_user_id,
            MemoryRow.agent_scope == agent_scope,
        ).order_by(
            MemoryRow.last_updated.desc(),
            MemoryRow.updated_at.desc(),
            MemoryRow.revision.desc(),
            MemoryRow.id.desc(),
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def _prune_duplicate_rows(session: Session, rows: list[MemoryRow]) -> MemoryRow | None:
        if not rows:
            return None
        for duplicate in rows[1:]:
            session.delete(duplicate)
        return rows[0]

    def _load_row(
        self,
        session: Session,
        owner_user_id: str,
        agent_scope: str,
        *,
        prune_duplicates: bool = False,
    ) -> MemoryRow | None:
        rows = self._load_rows(session, owner_user_id, agent_scope)
        if prune_duplicates:
            return self._prune_duplicate_rows(session, rows)
        return rows[0] if rows else None

    def _load_revision(self, session: Session, owner_user_id: str, agent_scope: str) -> int:
        row = self._load_row(session, owner_user_id, agent_scope)
        return row.revision if row is not None else 0

    @staticmethod
    def _copy_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(memory_data)

    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        owner_user_id, agent_scope = self._scope(agent_name, user_id=user_id)
        cache_key = (owner_user_id, agent_scope)
        with self._cache_lock:
            cached = self._memory_cache.get(cache_key)

        with self._session_factory() as session:
            if cached is not None:
                current_revision = self._load_revision(session, owner_user_id, agent_scope)
                if cached[1] == current_revision:
                    return self._copy_memory(cached[0])
            row = self._load_row(session, owner_user_id, agent_scope)
            if row is None:
                memory_data = create_empty_memory()
                revision = 0
            else:
                memory_data = self._copy_memory(row.memory_json)
                revision = row.revision

        with self._cache_lock:
            self._memory_cache[cache_key] = (self._copy_memory(memory_data), revision)
        return self._copy_memory(memory_data)

    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        owner_user_id, agent_scope = self._scope(agent_name, user_id=user_id)
        cache_key = (owner_user_id, agent_scope)
        with self._session_factory() as session:
            row = self._load_row(session, owner_user_id, agent_scope)
            if row is None:
                memory_data = create_empty_memory()
                revision = 0
            else:
                memory_data = self._copy_memory(row.memory_json)
                revision = row.revision

        with self._cache_lock:
            self._memory_cache[cache_key] = (self._copy_memory(memory_data), revision)
        return self._copy_memory(memory_data)

    def save(self, memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
        owner_user_id, agent_scope = self._scope(agent_name, user_id=user_id)
        cache_key = (owner_user_id, agent_scope)
        saved_memory = self._copy_memory(memory_data)
        saved_memory["lastUpdated"] = utc_now_iso_z()
        now = datetime.now(UTC)
        with self._cache_lock:
            cached = self._memory_cache.get(cache_key)
            expected_revision = cached[1] if cached is not None else None
        try:
            with self._session_factory() as session:
                row = self._load_row(session, owner_user_id, agent_scope, prune_duplicates=True)
                if row is None:
                    if expected_revision not in (None, 0):
                        logger.warning(
                            "Refusing stale memory save for user=%s agent=%s: expected revision %s but row is missing",
                            owner_user_id,
                            agent_scope,
                            expected_revision,
                        )
                        return False
                    row = MemoryRow(
                        owner_user_id=owner_user_id,
                        agent_scope=agent_scope,
                        memory_json=saved_memory,
                        schema_version=str(saved_memory.get("version", "1.0")),
                        revision=1,
                        last_updated=now,
                    )
                    session.add(row)
                else:
                    if expected_revision is not None and row.revision != expected_revision:
                        logger.warning(
                            "Refusing stale memory save for user=%s agent=%s: expected revision %s but found %s",
                            owner_user_id,
                            agent_scope,
                            expected_revision,
                            row.revision,
                        )
                        return False
                    row.memory_json = saved_memory
                    row.schema_version = str(saved_memory.get("version", "1.0"))
                    row.revision += 1
                    row.last_updated = now
                session.commit()
                revision = row.revision

            with self._cache_lock:
                self._memory_cache[cache_key] = (self._copy_memory(saved_memory), revision)
            return True
        except Exception:
            logger.exception("Failed to save memory row")
            return False


_storage_instance: MemoryStorage | None = None
_storage_lock = threading.Lock()


def get_memory_storage() -> MemoryStorage:
    """Get the configured memory storage instance."""
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    with _storage_lock:
        if _storage_instance is not None:
            return _storage_instance

        config = get_memory_config()
        storage_class_path = config.storage_class
        from deerflow.config.bootstrap import is_db_config_enabled

        db_mode = is_db_config_enabled()
        if db_mode and storage_class_path == _FILE_MEMORY_STORAGE_CLASS:
            storage_class_path = _DB_MEMORY_STORAGE_CLASS

        try:
            module_path, class_name = storage_class_path.rsplit(".", 1)
            import importlib

            module = importlib.import_module(module_path)
            storage_class = getattr(module, class_name)

            # Validate that the configured storage is a MemoryStorage implementation
            if not isinstance(storage_class, type):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a class: {storage_class!r}")
            if not issubclass(storage_class, MemoryStorage):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a subclass of MemoryStorage")

            _storage_instance = storage_class()
        except Exception as e:
            if db_mode:
                logger.error(
                    "Failed to load memory storage %s in DB config mode; refusing to fall back to file-backed memory: %s",
                    storage_class_path,
                    e,
                    exc_info=True,
                )
                _storage_instance = None
                raise
            logger.error(
                "Failed to load memory storage %s, falling back to FileMemoryStorage: %s",
                storage_class_path,
                e,
            )
            _storage_instance = FileMemoryStorage()

    return _storage_instance
