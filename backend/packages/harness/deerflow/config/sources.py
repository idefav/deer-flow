"""Runtime configuration source abstractions.

This layer is intentionally payload-only. It does not validate AppConfig and it
does not own extensions/MCP state; those responsibilities stay in the higher
config loader and the future ExtensionsConfigSource.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from deerflow.persistence.runtime_config.sql import RuntimeConfigRepository, stable_json_hash


@dataclass(frozen=True)
class RevisionedPayload:
    payload: dict[str, Any]
    revision: int
    content_hash: str
    updated_at: datetime
    updated_by: str | None = None


class FileConfigSourceProtocol(Protocol):
    def load_app_config_payload(self) -> RevisionedPayload:
        ...


class DbConfigSourceProtocol(Protocol):
    async def load_app_config_payload(self) -> RevisionedPayload:
        ...


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(payload)


class FileConfigSource:
    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path

    def load_app_config_payload(self) -> RevisionedPayload:
        from deerflow.config.app_config import AppConfig

        resolved_path = AppConfig.resolve_config_path(self._config_path)
        stat_result = resolved_path.stat()
        with Path(resolved_path).open(encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"Config file must contain a mapping at top level: {resolved_path}")
        payload_copy = _copy_payload(payload)
        return RevisionedPayload(
            payload=payload_copy,
            revision=stat_result.st_mtime_ns,
            content_hash=stable_json_hash(payload_copy),
            updated_at=datetime.fromtimestamp(stat_result.st_mtime, UTC),
        )


class DbConfigSource:
    def __init__(self, repository: RuntimeConfigRepository, *, key: str = "app") -> None:
        self._repository = repository
        self._key = key

    async def load_app_config_payload(self) -> RevisionedPayload:
        row = await self._repository.load(self._key)
        if row is None:
            raise KeyError(f"DB runtime config key {self._key!r} not found")
        return RevisionedPayload(
            payload=_copy_payload(row.payload),
            revision=row.revision,
            content_hash=row.content_hash,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )
