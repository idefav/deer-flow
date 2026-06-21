"""Repository for DB-backed Harness runtime configuration payloads."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.config.reload_boundary import STARTUP_ONLY_FIELDS
from deerflow.persistence.runtime_config.model import RuntimeConfigRow


def stable_json_hash(value: Any) -> str:
    """Return a deterministic SHA-256 hash for JSON-compatible values."""
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RuntimeConfigPayload:
    key: str
    payload: dict[str, Any]
    schema_version: str
    revision: int
    content_hash: str
    created_at: datetime
    updated_at: datetime
    updated_by: str | None
    changed_fields: tuple[str, ...] = ()
    restart_required_fields: tuple[str, ...] = ()
    restart_required_reasons: dict[str, str] = field(default_factory=dict)

    @property
    def requires_restart(self) -> bool:
        return bool(self.restart_required_fields)


def changed_top_level_fields(previous: dict[str, Any], current: dict[str, Any]) -> tuple[str, ...]:
    """Return sorted top-level keys whose values changed between payloads."""
    keys = set(previous) | set(current)
    return tuple(sorted(key for key in keys if previous.get(key) != current.get(key)))


def restart_required_reasons(changed_fields: tuple[str, ...]) -> dict[str, str]:
    """Return restart-required reasons for changed top-level AppConfig fields."""
    return {field: STARTUP_ONLY_FIELDS[field] for field in changed_fields if field in STARTUP_ONLY_FIELDS}


class RuntimeConfigRepository:
    """Short-lived-session repository for runtime config rows."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(payload)

    @classmethod
    def _row_to_payload(cls, row: RuntimeConfigRow) -> RuntimeConfigPayload:
        return RuntimeConfigPayload(
            key=row.key,
            payload=cls._copy_payload(row.payload_json),
            schema_version=row.schema_version,
            revision=row.revision,
            content_hash=row.content_hash,
            created_at=row.created_at,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
            restart_required_reasons={},
        )

    async def load(self, key: str) -> RuntimeConfigPayload | None:
        async with self._sf() as session:
            row = await session.get(RuntimeConfigRow, key)
            if row is None:
                return None
            return self._row_to_payload(row)

    async def upsert(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        schema_version: str = "1",
        updated_by: str | None = None,
    ) -> RuntimeConfigPayload:
        payload_copy = self._copy_payload(payload)
        content_hash = stable_json_hash(payload_copy)
        async with self._sf() as session:
            row = await session.get(RuntimeConfigRow, key)
            previous_payload = self._copy_payload(row.payload_json) if row is not None else {}
            if row is None:
                row = RuntimeConfigRow(
                    key=key,
                    payload_json=payload_copy,
                    schema_version=schema_version,
                    revision=1,
                    content_hash=content_hash,
                    updated_by=updated_by,
                )
                session.add(row)
            else:
                row.payload_json = payload_copy
                row.schema_version = schema_version
                row.revision += 1
                row.content_hash = content_hash
                row.updated_by = updated_by
            await session.commit()
            await session.refresh(row)
            result = self._row_to_payload(row)
            changed_fields = changed_top_level_fields(previous_payload, payload_copy) if previous_payload else ()
            required_reasons = restart_required_reasons(changed_fields)
            return RuntimeConfigPayload(
                key=result.key,
                payload=result.payload,
                schema_version=result.schema_version,
                revision=result.revision,
                content_hash=result.content_hash,
                created_at=result.created_at,
                updated_at=result.updated_at,
                updated_by=result.updated_by,
                changed_fields=changed_fields,
                restart_required_fields=tuple(required_reasons),
                restart_required_reasons=required_reasons,
            )
