"""DB-backed MCP server store."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from deerflow.config.database_config import DatabaseConfig
from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig
from deerflow.config.extensions_sources import RevisionedExtensionsConfig
from deerflow.persistence.base import Base
from deerflow.persistence.mcp.model import McpServerRow
from deerflow.persistence.runtime_config.model import RuntimeConfigRow
from deerflow.persistence.runtime_config.sql import stable_json_hash

_EMPTY_EXTENSIONS_PAYLOAD = {"mcpServers": {}, "skills": {}}
_EXTENSIONS_RUNTIME_CONFIG_KEY = "extensions"
_MCP_SERVER_KNOWN_KEYS = {
    "enabled",
    "type",
    "command",
    "args",
    "env",
    "url",
    "headers",
    "oauth",
    "description",
}


class McpServerConfigConflictError(RuntimeError):
    """Raised when saving MCP servers from a stale DB snapshot."""


class DbMcpServerStore:
    """Store MCP server definitions as first-class DB rows."""

    def __init__(self, *, database_config: DatabaseConfig) -> None:
        self._database_config = database_config
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
        raise ValueError("DbMcpServerStore requires sqlite or postgres database backend")

    @staticmethod
    def _empty_revisioned() -> RevisionedExtensionsConfig:
        payload = copy.deepcopy(_EMPTY_EXTENSIONS_PAYLOAD)
        return RevisionedExtensionsConfig(
            config=ExtensionsConfig.model_validate(payload),
            revision=0,
            content_hash=stable_json_hash(payload),
            updated_at=datetime.fromtimestamp(0, UTC),
        )

    @staticmethod
    def _server_to_payload(server: McpServerConfig) -> dict[str, Any]:
        return server.model_dump()

    @classmethod
    def _server_content_hash(cls, server: McpServerConfig) -> str:
        return stable_json_hash(cls._server_to_payload(server))

    @staticmethod
    def _server_to_row_values(server: McpServerConfig) -> dict[str, Any]:
        payload = server.model_dump()
        oauth_payload = copy.deepcopy(payload.get("oauth"))
        extra = {key: copy.deepcopy(value) for key, value in payload.items() if key not in _MCP_SERVER_KNOWN_KEYS}
        return {
            "enabled": bool(payload.get("enabled", True)),
            "transport_type": payload.get("type") or "stdio",
            "command": payload.get("command"),
            "args_json": copy.deepcopy(payload.get("args") or []),
            "url": payload.get("url"),
            "env_json": copy.deepcopy(payload.get("env") or {}),
            "headers_json": copy.deepcopy(payload.get("headers") or {}),
            "oauth_json": oauth_payload,
            "description": payload.get("description") or "",
            "extra_json": extra,
            "content_hash": stable_json_hash(payload),
        }

    @staticmethod
    def _row_to_server(row: McpServerRow) -> McpServerConfig:
        payload = copy.deepcopy(row.extra_json or {})
        payload.update(
            {
                "enabled": row.enabled,
                "type": row.transport_type,
                "command": row.command,
                "args": copy.deepcopy(row.args_json or []),
                "env": copy.deepcopy(row.env_json or {}),
                "url": row.url,
                "headers": copy.deepcopy(row.headers_json or {}),
                "description": row.description,
            }
        )
        if row.oauth_json is not None:
            payload["oauth"] = copy.deepcopy(row.oauth_json)
        return McpServerConfig.model_validate(payload)

    @classmethod
    def _rows_to_revisioned(cls, rows: list[McpServerRow]) -> RevisionedExtensionsConfig:
        if not rows:
            return cls._empty_revisioned()
        servers = {row.name: cls._row_to_server(row) for row in sorted(rows, key=lambda item: item.name)}
        payload = {
            "mcpServers": {name: cls._server_to_payload(server) for name, server in servers.items()},
            "skills": {},
        }
        latest_row = max(rows, key=lambda row: row.updated_at)
        return RevisionedExtensionsConfig(
            config=ExtensionsConfig.model_validate(copy.deepcopy(payload)),
            revision=sum(row.revision for row in rows),
            content_hash=stable_json_hash(payload),
            updated_at=latest_row.updated_at,
            updated_by=latest_row.updated_by,
        )

    @staticmethod
    def _list_rows(session: Session) -> list[McpServerRow]:
        stmt = select(McpServerRow).order_by(McpServerRow.name.asc())
        return list(session.execute(stmt).scalars())

    def load_extensions_config(self) -> RevisionedExtensionsConfig:
        with self._session_factory() as session:
            return self._rows_to_revisioned(self._list_rows(session))

    @classmethod
    def save_mcp_servers_in_session(
        cls,
        session: Session,
        servers: dict[str, McpServerConfig | dict],
        *,
        updated_by: str | None = None,
        expected_revision: int | None = None,
        expected_content_hash: str | None = None,
    ) -> RevisionedExtensionsConfig:
        normalized = {
            name: server if isinstance(server, McpServerConfig) else McpServerConfig.model_validate(server)
            for name, server in servers.items()
        }

        current_rows = cls._list_rows(session)
        current = cls._rows_to_revisioned(current_rows)
        if expected_revision is not None and current.revision != expected_revision:
            raise McpServerConfigConflictError(
                f"MCP server config revision conflict: expected {expected_revision}, found {current.revision}"
            )
        if expected_content_hash is not None and current.content_hash != expected_content_hash:
            raise McpServerConfigConflictError(
                f"MCP server config hash conflict: expected {expected_content_hash}, found {current.content_hash}"
            )

        existing = {row.name: row for row in current_rows}
        for name, row in existing.items():
            if name not in normalized:
                session.delete(row)

        for name, server in normalized.items():
            values = cls._server_to_row_values(server)
            row = existing.get(name)
            if row is None:
                session.add(
                    McpServerRow(
                        name=name,
                        revision=1,
                        updated_by=updated_by,
                        **values,
                    )
                )
                continue
            if row.content_hash != values["content_hash"]:
                for key, value in values.items():
                    setattr(row, key, value)
                row.revision += 1
            row.updated_by = updated_by

        session.flush()
        return cls._rows_to_revisioned(cls._list_rows(session))

    @staticmethod
    def _clean_extensions_runtime_payload(payload: dict | None) -> dict:
        cleaned = copy.deepcopy(payload or {})
        cleaned.pop("mcpServers", None)
        cleaned.setdefault("skills", {})
        return cleaned

    @classmethod
    def bump_extensions_runtime_revision_in_session(
        cls,
        session: Session,
        *,
        updated_by: str | None = None,
        key: str = _EXTENSIONS_RUNTIME_CONFIG_KEY,
    ) -> RuntimeConfigRow:
        row = session.get(RuntimeConfigRow, key)
        if row is None:
            payload = {"skills": {}}
            row = RuntimeConfigRow(
                key=key,
                payload_json=payload,
                schema_version="1",
                revision=1,
                content_hash=stable_json_hash(payload),
                updated_by=updated_by,
            )
            session.add(row)
            session.flush()
            return row

        payload = cls._clean_extensions_runtime_payload(row.payload_json)
        row.payload_json = payload
        row.schema_version = "1"
        row.revision += 1
        row.content_hash = stable_json_hash(payload)
        row.updated_by = updated_by
        session.flush()
        return row

    def save_mcp_servers(
        self,
        servers: dict[str, McpServerConfig | dict],
        *,
        updated_by: str | None = None,
        expected_revision: int | None = None,
        expected_content_hash: str | None = None,
    ) -> RevisionedExtensionsConfig:
        with self._session_factory() as session:
            self.save_mcp_servers_in_session(
                session,
                servers,
                updated_by=updated_by,
                expected_revision=expected_revision,
                expected_content_hash=expected_content_hash,
            )
            self.bump_extensions_runtime_revision_in_session(session, updated_by=updated_by)
            session.commit()

        return self.load_extensions_config()
