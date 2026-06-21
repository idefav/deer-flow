"""Extensions configuration source abstractions for file and DB modes."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import deerflow.persistence.models  # noqa: F401  # Ensure aggregate DB stores create every related table.
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.persistence.base import Base
from deerflow.persistence.runtime_config.model import RuntimeConfigRow
from deerflow.persistence.runtime_config.sql import RuntimeConfigRepository, stable_json_hash

_EMPTY_EXTENSIONS_PAYLOAD = {"mcpServers": {}, "skills": {}}


class ExtensionsConfigConflictError(RuntimeError):
    """Raised when a DB extensions config save sees a stale revision."""


@dataclass(frozen=True)
class RevisionedExtensionsConfig:
    config: ExtensionsConfig
    revision: int
    content_hash: str
    updated_at: datetime
    updated_by: str | None = None


def _empty_extensions() -> RevisionedExtensionsConfig:
    payload = copy.deepcopy(_EMPTY_EXTENSIONS_PAYLOAD)
    return RevisionedExtensionsConfig(
        config=ExtensionsConfig.model_validate(payload),
        revision=0,
        content_hash=stable_json_hash(payload),
        updated_at=datetime.fromtimestamp(0, UTC),
    )


class FileExtensionsConfigSource:
    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path

    def load_extensions_config(self) -> RevisionedExtensionsConfig:
        resolved_path = ExtensionsConfig.resolve_config_path(self._config_path)
        if resolved_path is None:
            return _empty_extensions()
        with Path(resolved_path).open(encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError(f"Extensions config file must contain a mapping at top level: {resolved_path}")
        stat_result = resolved_path.stat()
        return RevisionedExtensionsConfig(
            config=ExtensionsConfig.model_validate(ExtensionsConfig.resolve_env_variables(copy.deepcopy(payload))),
            revision=stat_result.st_mtime_ns,
            content_hash=stable_json_hash(payload),
            updated_at=datetime.fromtimestamp(stat_result.st_mtime, UTC),
        )


class DbExtensionsConfigSource:
    def __init__(self, repository: RuntimeConfigRepository, *, key: str = "extensions") -> None:
        self._repository = repository
        self._key = key

    async def load_extensions_config(self) -> RevisionedExtensionsConfig:
        row = await self._repository.load(self._key)
        if row is None:
            return _empty_extensions()
        return RevisionedExtensionsConfig(
            config=ExtensionsConfig.model_validate(copy.deepcopy(row.payload)),
            revision=row.revision,
            content_hash=row.content_hash,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )


class DbExtensionsConfigStore:
    """Synchronous DB store for runtime extensions config.

    This is used by sync runtime paths and HTTP handlers that need to update the
    same ``runtime_configs.extensions`` payload loaded at async startup.
    """

    def __init__(self, *, database_config: DatabaseConfig, key: str = "extensions") -> None:
        self._database_config = database_config
        self._key = key
        self._engine = create_engine(self._sync_sqlalchemy_url(database_config))
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

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
        raise ValueError("DbExtensionsConfigStore requires sqlite or postgres database backend")

    def _load_mcp_extensions_config(self) -> RevisionedExtensionsConfig:
        from deerflow.config.mcp_store import DbMcpServerStore

        return DbMcpServerStore(database_config=self._database_config).load_extensions_config()

    @staticmethod
    def _mcp_payload(config: ExtensionsConfig) -> dict:
        return {name: server.model_dump() for name, server in config.mcp_servers.items()}

    def load_extensions_config(self) -> RevisionedExtensionsConfig:
        mcp_loaded = self._load_mcp_extensions_config()
        with self._session_factory() as session:
            row = session.get(RuntimeConfigRow, self._key)
            if row is None:
                if not mcp_loaded.config.mcp_servers:
                    return _empty_extensions()
                payload = {
                    "mcpServers": self._mcp_payload(mcp_loaded.config),
                    "skills": {},
                }
                return RevisionedExtensionsConfig(
                    config=ExtensionsConfig.model_validate(copy.deepcopy(payload)),
                    revision=mcp_loaded.revision,
                    content_hash=stable_json_hash(payload),
                    updated_at=mcp_loaded.updated_at,
                    updated_by=mcp_loaded.updated_by,
            )
            payload = copy.deepcopy(row.payload_json)
            legacy_mcp_servers = payload.get("mcpServers")
            if not mcp_loaded.config.mcp_servers and isinstance(legacy_mcp_servers, dict) and legacy_mcp_servers:
                from deerflow.config.mcp_store import DbMcpServerStore

                mcp_loaded = DbMcpServerStore.save_mcp_servers_in_session(
                    session,
                    copy.deepcopy(legacy_mcp_servers),
                    updated_by=row.updated_by,
                )
                payload.pop("mcpServers", None)
                payload.setdefault("skills", {})
                row.payload_json = copy.deepcopy(payload)
                row.schema_version = "1"
                row.revision += 1
                row.content_hash = stable_json_hash(payload)
                session.commit()
                session.refresh(row)
            if mcp_loaded.config.mcp_servers:
                payload["mcpServers"] = self._mcp_payload(mcp_loaded.config)
            else:
                payload.setdefault("mcpServers", {})
            payload.setdefault("skills", {})
            return RevisionedExtensionsConfig(
                config=ExtensionsConfig.model_validate(copy.deepcopy(payload)),
                revision=row.revision,
                content_hash=stable_json_hash(payload),
                updated_at=row.updated_at,
                updated_by=row.updated_by,
            )

    def save_extensions_config(
        self,
        config: ExtensionsConfig,
        *,
        updated_by: str | None = None,
        expected_revision: int | None = None,
    ) -> RevisionedExtensionsConfig:
        payload = config.model_dump(by_alias=True)
        mcp_servers = payload.pop("mcpServers", {})
        payload.setdefault("skills", {})
        content_hash = stable_json_hash(payload)
        with self._session_factory() as session:
            row = session.get(RuntimeConfigRow, self._key)
            if row is None:
                if expected_revision not in (None, 0):
                    raise ExtensionsConfigConflictError(
                        f"Extensions config revision conflict: expected {expected_revision}, found missing row"
                    )
            elif expected_revision is not None and row.revision != expected_revision:
                raise ExtensionsConfigConflictError(
                    f"Extensions config revision conflict: expected {expected_revision}, found {row.revision}"
                )

            from deerflow.config.mcp_store import DbMcpServerStore

            DbMcpServerStore.save_mcp_servers_in_session(
                session,
                mcp_servers,
                updated_by=updated_by,
            )

            if row is None:
                row = RuntimeConfigRow(
                    key=self._key,
                    payload_json=copy.deepcopy(payload),
                    schema_version="1",
                    revision=1,
                    content_hash=content_hash,
                    updated_by=updated_by,
                )
                session.add(row)
            else:
                row.payload_json = copy.deepcopy(payload)
                row.schema_version = "1"
                row.revision += 1
                row.content_hash = content_hash
                row.updated_by = updated_by
            session.commit()
        return self.load_extensions_config()
