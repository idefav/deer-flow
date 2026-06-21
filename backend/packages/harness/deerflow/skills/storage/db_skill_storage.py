"""DB-backed implementation of ``SkillStorage``.

Public and custom skills are stored in normalized DB rows and materialized into
``host_path`` only as a temporary runtime cache. The legacy ``custom_skills``
table is still maintained for backward-compatible migrations.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import posixpath
import shutil
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from deerflow.config.app_config import get_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.runtime_paths import resolve_path
from deerflow.persistence.base import Base
from deerflow.persistence.skills.model import NormalizedSkillRow, SkillFileRow, SkillHistoryRow, SkillRow
from deerflow.skills.storage.local_skill_storage import DEFAULT_SKILLS_CONTAINER_PATH
from deerflow.skills.storage.skill_storage import ALLOWED_SUPPORT_SUBDIRS, SKILL_MD_FILE, SkillFileManifest, SkillStorage
from deerflow.skills.types import Skill, SkillCategory

logger = logging.getLogger(__name__)


class DbSkillStorage(SkillStorage):
    """Skill storage backed by DB rows plus a materialized host cache."""

    _BINARY_FILE_ENCODING = "base64"
    _TEXT_FILE_ENCODING = "utf-8"

    def __init__(
        self,
        host_path: str | None = None,
        container_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
        *,
        database_config: DatabaseConfig | None = None,
        app_config=None,
    ) -> None:
        super().__init__(container_path=container_path)
        config = app_config
        if config is None and (database_config is None or host_path is None):
            config = get_app_config()
        if database_config is None:
            if config is None:
                raise ValueError("DbSkillStorage requires database_config when app_config is not provided")
            database_config = config.database
        if host_path is None:
            if config is None:
                raise ValueError("DbSkillStorage requires host_path when app_config is not provided")
            host_path = str(config.skills.get_skills_path())
        self._database_config = database_config
        self._host_root = resolve_path(host_path)
        self._host_root.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(self._sync_sqlalchemy_url(self._database_config))
        Base.metadata.create_all(self._engine)
        self._ensure_schema_compatibility()
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)
        self._owner_user_id = ""
        self._backfill_legacy_custom_rows()

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
        raise ValueError("DbSkillStorage requires sqlite or postgres database backend")

    def _ensure_schema_compatibility(self) -> None:
        """Add columns introduced after the initial prototype tables.

        The DB-backed skill store is still using ``create_all`` in this branch,
        so an existing local DB can have the table without newer columns.
        """
        inspector = inspect(self._engine)
        if "custom_skills" not in inspector.get_table_names():
            return
        column_names = {column["name"] for column in inspector.get_columns("custom_skills")}
        if "metadata_json" in column_names:
            return

        if self._database_config.backend == "postgres":
            statement = "ALTER TABLE custom_skills ADD COLUMN IF NOT EXISTS metadata_json JSON NOT NULL DEFAULT '{}'::json"
        else:
            statement = "ALTER TABLE custom_skills ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'"
        with self._engine.begin() as connection:
            connection.execute(text(statement))

    def get_skills_root_path(self) -> Path:
        return self._host_root

    def _custom_skill_dir(self, name: str) -> Path:
        return self._host_root / SkillCategory.CUSTOM.value / self.validate_skill_name(name)

    def _skill_dir(self, category: SkillCategory | str, name: str) -> Path:
        skill_category = SkillCategory(category)
        return self._host_root / skill_category.value / self.validate_skill_name(name)

    def get_custom_skill_dir(self, name: str) -> Path:
        normalized_name = self.validate_skill_name(name)
        row = self._load_normalized_row_by_name(normalized_name, SkillCategory.CUSTOM)
        if row is not None:
            self._materialize_normalized_row(row, include_support=True)
        return self._custom_skill_dir(normalized_name)

    def get_skill_history_file(self, name: str) -> Path:
        normalized_name = self.validate_skill_name(name)
        history_path = self._host_root / SkillCategory.CUSTOM.value / ".history" / f"{normalized_name}.jsonl"
        records = self.read_history(normalized_name)
        if records:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
        return history_path

    def _load_rows(self, session: Session, name: str) -> list[SkillRow]:
        stmt = select(SkillRow).where(
            SkillRow.owner_user_id == self._owner_user_id,
            SkillRow.skill_name == name,
        ).order_by(
            SkillRow.updated_at.desc(),
            SkillRow.revision.desc(),
            SkillRow.id.desc(),
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def _prune_duplicate_rows(session: Session, rows: list[Any]) -> Any | None:
        if not rows:
            return None
        for duplicate in rows[1:]:
            session.delete(duplicate)
        return rows[0]

    def _load_row(self, session: Session, name: str, *, prune_duplicates: bool = False) -> SkillRow | None:
        rows = self._load_rows(session, name)
        if prune_duplicates:
            return self._prune_duplicate_rows(session, rows)
        return rows[0] if rows else None

    def _load_row_by_name(self, name: str) -> SkillRow | None:
        with self._session_factory() as session:
            return self._load_row(session, name)

    def _list_rows(self) -> list[SkillRow]:
        stmt = select(SkillRow).where(SkillRow.owner_user_id == self._owner_user_id).order_by(SkillRow.skill_name.asc())
        with self._session_factory() as session:
            return list(session.execute(stmt).scalars())

    def _load_normalized_rows(
        self,
        session: Session,
        name: str,
        category: SkillCategory | str,
    ) -> list[NormalizedSkillRow]:
        skill_category = SkillCategory(category)
        stmt = select(NormalizedSkillRow).where(
            NormalizedSkillRow.owner_user_id == self._owner_user_id,
            NormalizedSkillRow.category == skill_category.value,
            NormalizedSkillRow.skill_name == name,
        ).order_by(
            NormalizedSkillRow.updated_at.desc(),
            NormalizedSkillRow.revision.desc(),
            NormalizedSkillRow.id.desc(),
        )
        return list(session.execute(stmt).scalars())

    def _load_normalized_row(
        self,
        session: Session,
        name: str,
        category: SkillCategory | str,
        *,
        prune_duplicates: bool = False,
    ) -> NormalizedSkillRow | None:
        rows = self._load_normalized_rows(session, name, category)
        if prune_duplicates:
            return self._prune_duplicate_normalized_rows(session, rows)
        return rows[0] if rows else None

    def _prune_duplicate_normalized_rows(
        self,
        session: Session,
        rows: list[NormalizedSkillRow],
    ) -> NormalizedSkillRow | None:
        if not rows:
            return None
        for duplicate in rows[1:]:
            for file_row in self._list_all_skill_file_rows(session, skill_id=duplicate.id):
                session.delete(file_row)
            session.delete(duplicate)
        return rows[0]

    def _load_normalized_row_by_name(self, name: str, category: SkillCategory | str) -> NormalizedSkillRow | None:
        normalized_name = self.validate_skill_name(name)
        with self._session_factory() as session:
            return self._load_normalized_row(session, normalized_name, category)

    def _list_normalized_rows(self, category: SkillCategory | str | None = None) -> list[NormalizedSkillRow]:
        stmt = select(NormalizedSkillRow).where(NormalizedSkillRow.owner_user_id == self._owner_user_id)
        if category is not None:
            stmt = stmt.where(NormalizedSkillRow.category == SkillCategory(category).value)
        stmt = stmt.order_by(
            NormalizedSkillRow.category.asc(),
            NormalizedSkillRow.skill_name.asc(),
            NormalizedSkillRow.updated_at.desc(),
            NormalizedSkillRow.revision.desc(),
            NormalizedSkillRow.id.desc(),
        )
        with self._session_factory() as session:
            rows_by_key: dict[tuple[str, str], NormalizedSkillRow] = {}
            for row in session.execute(stmt).scalars():
                rows_by_key.setdefault((row.category, row.skill_name), row)
            return list(rows_by_key.values())

    @staticmethod
    def _load_skill_file_rows(
        session: Session,
        *,
        skill_id: int,
        relative_path: str,
    ) -> list[SkillFileRow]:
        stmt = select(SkillFileRow).where(
            SkillFileRow.skill_id == skill_id,
            SkillFileRow.relative_path == relative_path,
        ).order_by(
            SkillFileRow.updated_at.desc(),
            SkillFileRow.id.desc(),
        )
        return list(session.execute(stmt).scalars())

    @classmethod
    def _load_skill_file_row(
        cls,
        session: Session,
        *,
        skill_id: int,
        relative_path: str,
        prune_duplicates: bool = False,
    ) -> SkillFileRow | None:
        rows = cls._load_skill_file_rows(session, skill_id=skill_id, relative_path=relative_path)
        if prune_duplicates:
            return cls._prune_duplicate_rows(session, rows)
        return rows[0] if rows else None

    @staticmethod
    def _list_all_skill_file_rows(session: Session, *, skill_id: int) -> list[SkillFileRow]:
        stmt = select(SkillFileRow).where(SkillFileRow.skill_id == skill_id)
        return list(session.execute(stmt).scalars())

    @staticmethod
    def _list_skill_file_rows(session: Session, *, skill_id: int, prune_duplicates: bool = False) -> list[SkillFileRow]:
        stmt = select(SkillFileRow).where(SkillFileRow.skill_id == skill_id).order_by(
            SkillFileRow.relative_path.asc(),
            SkillFileRow.updated_at.desc(),
            SkillFileRow.id.desc(),
        )
        rows_by_path: dict[str, list[SkillFileRow]] = {}
        for row in session.execute(stmt).scalars():
            rows_by_path.setdefault(row.relative_path, []).append(row)
        rows: list[SkillFileRow] = []
        for path_rows in rows_by_path.values():
            if prune_duplicates:
                for duplicate in path_rows[1:]:
                    session.delete(duplicate)
            rows.append(path_rows[0])
        return rows

    @staticmethod
    def _metadata_payload_from_skill(skill: Skill) -> dict[str, Any]:
        return {
            "name": skill.name,
            "description": skill.description,
            "license": skill.license,
            "allowed_tools": skill.allowed_tools,
        }

    def _extract_metadata_from_skill_md(
        self,
        name: str,
        content: str,
        *,
        category: SkillCategory = SkillCategory.CUSTOM,
    ) -> dict[str, Any] | None:
        from deerflow.skills.parser import parse_skill_file

        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_dir = Path(tmp_dir) / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file = skill_dir / SKILL_MD_FILE
            skill_file.write_text(content, encoding="utf-8")
            skill = parse_skill_file(skill_file, category=category, relative_path=Path(name))

        if skill is None:
            return None
        if skill.name != name:
            logger.warning("Skipping metadata cache for %s skill %s because frontmatter name is %s", category.value, name, skill.name)
            return None
        return self._metadata_payload_from_skill(skill)

    @staticmethod
    def _is_valid_metadata_payload(name: str, metadata: object) -> bool:
        if not isinstance(metadata, dict):
            return False
        if metadata.get("name") != name:
            return False
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            return False
        allowed_tools = metadata.get("allowed_tools")
        if allowed_tools is not None and not (
            isinstance(allowed_tools, list) and all(isinstance(item, str) and item.strip() for item in allowed_tools)
        ):
            return False
        return True

    def _metadata_for_row(self, row: SkillRow) -> dict[str, Any] | None:
        if self._is_valid_metadata_payload(row.skill_name, row.metadata_json):
            return copy.deepcopy(row.metadata_json)

        metadata = self._extract_metadata_from_skill_md(row.skill_name, row.skill_md_text)
        if metadata is None:
            return None

        with self._session_factory() as session:
            db_row = self._load_row(session, row.skill_name)
            if db_row is not None:
                db_row.metadata_json = metadata
                session.commit()
        return metadata

    def _metadata_for_normalized_row(self, row: NormalizedSkillRow) -> dict[str, Any] | None:
        if self._is_valid_metadata_payload(row.skill_name, row.metadata_json):
            return copy.deepcopy(row.metadata_json)

        with self._session_factory() as session:
            file_row = self._load_skill_file_row(session, skill_id=row.id, relative_path=SKILL_MD_FILE)
            if file_row is None:
                return None
            skill_md_content = self._deserialize_support_file_content(file_row.content_json)
            if not isinstance(skill_md_content, str):
                return None
            metadata = self._extract_metadata_from_skill_md(
                row.skill_name,
                skill_md_content,
                category=SkillCategory(row.category),
            )
            if metadata is None:
                return None
            db_row = session.get(NormalizedSkillRow, row.id)
            if db_row is not None:
                db_row.metadata_json = metadata
                session.commit()
        return metadata

    def _skill_from_custom_row(self, row: SkillRow) -> Skill | None:
        metadata = self._metadata_for_row(row)
        if metadata is None:
            return None

        skill_dir = self._custom_skill_dir(row.skill_name)
        return Skill(
            name=row.skill_name,
            description=str(metadata["description"]).strip(),
            license=str(metadata["license"]).strip() if metadata.get("license") else None,
            skill_dir=skill_dir,
            skill_file=skill_dir / SKILL_MD_FILE,
            relative_path=Path(row.skill_name),
            category=SkillCategory.CUSTOM,
            allowed_tools=copy.deepcopy(metadata.get("allowed_tools")),
            enabled=True,
        )

    def _skill_from_normalized_row(self, row: NormalizedSkillRow) -> Skill | None:
        metadata = self._metadata_for_normalized_row(row)
        if metadata is None:
            return None

        category = SkillCategory(row.category)
        skill_dir = self._skill_dir(category, row.skill_name)
        return Skill(
            name=row.skill_name,
            description=str(metadata["description"]).strip(),
            license=str(metadata["license"]).strip() if metadata.get("license") else None,
            skill_dir=skill_dir,
            skill_file=skill_dir / SKILL_MD_FILE,
            relative_path=Path(row.skill_name),
            category=category,
            allowed_tools=copy.deepcopy(metadata.get("allowed_tools")),
            enabled=bool(row.enabled),
        )

    @staticmethod
    def _remove_materialized_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def _prune_materialized_support_dirs(self, skill_dir: Path) -> None:
        for dirname in ALLOWED_SUPPORT_SUBDIRS:
            path = skill_dir / dirname
            if path.exists() or path.is_symlink():
                self._remove_materialized_path(path)

    def _prune_stale_custom_skill_dirs(self, active_names: set[str]) -> None:
        self._prune_stale_skill_dirs(SkillCategory.CUSTOM, active_names)

    def _prune_stale_skill_dirs(self, category: SkillCategory | str, active_names: set[str]) -> None:
        skill_category = SkillCategory(category)
        category_root = self._host_root / skill_category.value
        if not category_root.exists():
            return
        for child in category_root.iterdir():
            if skill_category == SkillCategory.CUSTOM and child.name == ".history":
                continue
            if child.name in active_names:
                continue
            self._remove_materialized_path(child)

    def _materialize_row(self, row: SkillRow) -> None:
        skill_dir = self._custom_skill_dir(row.skill_name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        self._prune_materialized_support_dirs(skill_dir)
        (skill_dir / SKILL_MD_FILE).write_text(row.skill_md_text, encoding="utf-8")
        for relative_path, content in (row.files_json or {}).items():
            target = self.validate_relative_path(relative_path, skill_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_materialized_support_file(target, content)

    def _materialize_skill_md(self, row: SkillRow) -> None:
        skill_dir = self._custom_skill_dir(row.skill_name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / SKILL_MD_FILE).write_text(row.skill_md_text, encoding="utf-8")

    def _materialize_normalized_row(self, row: NormalizedSkillRow, *, include_support: bool) -> None:
        category = SkillCategory(row.category)
        skill_dir = self._skill_dir(category, row.skill_name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        if include_support:
            self._prune_materialized_support_dirs(skill_dir)

        with self._session_factory() as session:
            file_rows = self._list_skill_file_rows(session, skill_id=row.id)
            for file_row in file_rows:
                if file_row.relative_path != SKILL_MD_FILE and not include_support:
                    continue
                target = self.validate_relative_path(file_row.relative_path, skill_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._write_materialized_support_file(target, file_row.content_json)

    @staticmethod
    def _normalize_archive_member_name(name: str) -> str | None:
        normalized = posixpath.normpath(name.replace("\\", "/"))
        if normalized in {"", "."} or posixpath.isabs(normalized) or normalized.startswith("../"):
            return None
        return normalized

    @classmethod
    def _read_archive_file_modes(cls, archive) -> dict[str, int]:
        modes: dict[str, int] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            normalized = cls._normalize_archive_member_name(info.filename)
            if normalized is None:
                continue
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                modes[normalized] = mode
        return modes

    @staticmethod
    def _payload_mode(content: Any) -> int | None:
        if not isinstance(content, dict):
            return None
        mode = content.get("mode")
        if not isinstance(mode, int):
            return None
        return mode & 0o777

    @classmethod
    def _serialize_support_file_content(cls, content: str | bytes, *, mode: int | None = None) -> str | dict[str, Any]:
        if isinstance(content, bytes):
            payload: dict[str, Any] = {
                "encoding": cls._BINARY_FILE_ENCODING,
                "data": base64.b64encode(content).decode("ascii"),
            }
        elif mode is not None:
            payload = {
                "encoding": cls._TEXT_FILE_ENCODING,
                "text": content,
            }
        else:
            return content

        if mode is not None:
            payload["mode"] = mode & 0o777
        return payload

    @classmethod
    def _write_materialized_support_file(cls, target: Path, content: Any) -> None:
        mode = cls._payload_mode(content)
        if isinstance(content, dict):
            encoding = content.get("encoding")
            if encoding == cls._BINARY_FILE_ENCODING:
                data = content.get("data")
                if not isinstance(data, str):
                    raise ValueError(f"Binary skill support file payload for {target} is missing base64 data.")
                target.write_bytes(base64.b64decode(data.encode("ascii")))
            elif encoding == cls._TEXT_FILE_ENCODING:
                text = content.get("text")
                if not isinstance(text, str):
                    raise ValueError(f"Text skill support file payload for {target} is missing text data.")
                target.write_text(text, encoding="utf-8")
            else:
                target.write_text(str(content), encoding="utf-8")
            if mode is not None:
                target.chmod(mode)
            return
        target.write_text(str(content), encoding="utf-8")

    @classmethod
    def _support_file_bytes(cls, content: Any) -> tuple[bytes, bool]:
        if isinstance(content, dict):
            encoding = content.get("encoding")
            if encoding == cls._BINARY_FILE_ENCODING:
                data = content.get("data")
                if not isinstance(data, str):
                    raise ValueError("Binary skill support file payload is missing base64 data.")
                return base64.b64decode(data.encode("ascii")), True
            if encoding == cls._TEXT_FILE_ENCODING:
                text = content.get("text")
                if not isinstance(text, str):
                    raise ValueError("Text skill support file payload is missing text data.")
                return text.encode("utf-8"), False
        return str(content).encode("utf-8"), False

    @classmethod
    def _deserialize_support_file_content(cls, content: Any) -> str | bytes:
        data, binary = cls._support_file_bytes(content)
        return data if binary else data.decode("utf-8")

    @classmethod
    def _file_manifest_values(cls, relative_path: str, content: Any) -> dict[str, Any]:
        data, binary = cls._support_file_bytes(content)
        return {
            "content_hash": hashlib.sha256(data).hexdigest(),
            "mime_type": cls._mime_type_for_relative_path(relative_path, binary=binary),
            "size": len(data),
            "mode": cls._payload_mode(content),
        }

    @staticmethod
    def _skill_content_hash(file_values: dict[str, dict[str, Any]]) -> str:
        payload = {path: values["content_hash"] for path, values in sorted(file_values.items())}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _normalized_files_from_payloads(
        self,
        skill_md_text: str,
        support_files: dict[str, Any] | None,
    ) -> dict[str, Any]:
        files = {SKILL_MD_FILE: skill_md_text}
        files.update(copy.deepcopy(support_files or {}))
        return files

    def _upsert_normalized_skill(
        self,
        session: Session,
        *,
        category: SkillCategory,
        name: str,
        skill_md_text: str,
        support_files: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
        revision: int,
        source: str,
    ) -> NormalizedSkillRow:
        normalized_name = self.validate_skill_name(name)
        files = self._normalized_files_from_payloads(skill_md_text, support_files)
        file_values = {relative_path: self._file_manifest_values(relative_path, content) for relative_path, content in files.items()}
        row = self._load_normalized_row(session, normalized_name, category, prune_duplicates=True)
        values = {
            "metadata_json": copy.deepcopy(metadata_json or {}),
            "enabled": True,
            "revision": revision,
            "source": source,
            "content_hash": self._skill_content_hash(file_values),
        }
        if row is None:
            row = NormalizedSkillRow(
                owner_user_id=self._owner_user_id,
                category=category.value,
                skill_name=normalized_name,
                **values,
            )
            session.add(row)
            session.flush()
        else:
            for key, value in values.items():
                setattr(row, key, value)
            session.flush()

        existing_files = {
            file_row.relative_path: file_row
            for file_row in self._list_skill_file_rows(session, skill_id=row.id, prune_duplicates=True)
        }
        for relative_path, file_row in existing_files.items():
            if relative_path not in files:
                session.delete(file_row)

        for relative_path, content in sorted(files.items()):
            values = file_values[relative_path]
            file_row = existing_files.get(relative_path)
            payload = copy.deepcopy(content)
            if file_row is None:
                session.add(
                    SkillFileRow(
                        skill_id=row.id,
                        relative_path=relative_path,
                        content_json=payload,
                        mode=values["mode"],
                        mime_type=values["mime_type"],
                        size=values["size"],
                        content_hash=values["content_hash"],
                    )
                )
                continue
            file_row.content_json = payload
            file_row.mode = values["mode"]
            file_row.mime_type = values["mime_type"]
            file_row.size = values["size"]
            file_row.content_hash = values["content_hash"]

        session.flush()
        return row

    def _upsert_normalized_from_legacy_row(self, row: SkillRow) -> None:
        metadata = self._metadata_for_row(row) or {}
        with self._session_factory() as session:
            self._upsert_normalized_skill(
                session,
                category=SkillCategory.CUSTOM,
                name=row.skill_name,
                skill_md_text=row.skill_md_text,
                support_files=row.files_json or {},
                metadata_json=metadata,
                revision=row.revision,
                source="legacy-custom-skills",
            )
            session.commit()

    def _backfill_legacy_custom_rows(self) -> None:
        with self._session_factory() as session:
            legacy_rows = list(session.execute(select(SkillRow).where(SkillRow.owner_user_id == self._owner_user_id)).scalars())
            for legacy_row in legacy_rows:
                if self._load_normalized_row(session, legacy_row.skill_name, SkillCategory.CUSTOM) is not None:
                    continue
                metadata = self._metadata_for_row(legacy_row) or {}
                self._upsert_normalized_skill(
                    session,
                    category=SkillCategory.CUSTOM,
                    name=legacy_row.skill_name,
                    skill_md_text=legacy_row.skill_md_text,
                    support_files=legacy_row.files_json or {},
                    metadata_json=metadata,
                    revision=legacy_row.revision,
                    source="legacy-custom-skills-backfill",
                )
            session.commit()

    def _iter_skill_files(self) -> Iterable[tuple[SkillCategory, Path, Path]]:
        rows = self._list_normalized_rows()
        active_by_category: dict[SkillCategory, set[str]] = {category: set() for category in SkillCategory}
        for row in rows:
            active_by_category[SkillCategory(row.category)].add(row.skill_name)
        for category, active_names in active_by_category.items():
            self._prune_stale_skill_dirs(category, active_names)
        for row in rows:
            category = SkillCategory(row.category)
            self._materialize_normalized_row(row, include_support=False)
            yield category, self._host_root / category.value, self._skill_dir(category, row.skill_name) / SKILL_MD_FILE

    def load_skills(self, *, enabled_only: bool = False) -> list[Skill]:
        skills_by_name: dict[str, Skill] = {}
        rows = self._list_normalized_rows()
        active_by_category: dict[SkillCategory, set[str]] = {category: set() for category in SkillCategory}
        for row in rows:
            active_by_category[SkillCategory(row.category)].add(row.skill_name)
        for category, active_names in active_by_category.items():
            self._prune_stale_skill_dirs(category, active_names)
        for row in rows:
            self._materialize_normalized_row(row, include_support=False)
            skill = self._skill_from_normalized_row(row)
            if skill:
                skills_by_name[skill.name] = skill

        skills = list(skills_by_name.values())

        try:
            from deerflow.config.extensions_config import reload_extensions_config

            extensions_config = reload_extensions_config()
            for skill in skills:
                skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
        except Exception as e:
            logger.warning("Failed to load extensions config: %s", e)

        if enabled_only:
            skills = [skill for skill in skills if skill.enabled]

        skills.sort(key=lambda skill: skill.name)
        return skills

    def read_custom_skill(self, name: str) -> str:
        normalized_name = self.validate_skill_name(name)
        row = self._load_normalized_row_by_name(normalized_name, SkillCategory.CUSTOM)
        if row is None:
            raise FileNotFoundError(f"Custom skill '{name}' not found.")
        content = self.read_skill_file(normalized_name, SkillCategory.CUSTOM, SKILL_MD_FILE)
        if not isinstance(content, str):
            raise ValueError(f"Custom skill '{name}' has non-text SKILL.md content.")
        return content

    def _normalize_skill_relative_path(self, name: str, category: SkillCategory | str, relative_path: str) -> str:
        normalized_name = self.validate_skill_name(name)
        skill_category = SkillCategory(category)
        skill_dir = self._skill_dir(skill_category, normalized_name)
        target = self.validate_relative_path(relative_path, skill_dir)
        normalized_relative_path = target.relative_to(skill_dir.resolve()).as_posix()
        if normalized_relative_path == SKILL_MD_FILE:
            return normalized_relative_path
        relative = Path(normalized_relative_path)
        if not relative.parts or relative.parts[0] not in ALLOWED_SUPPORT_SUBDIRS:
            raise ValueError(f"Supporting files must live under one of: {', '.join(sorted(ALLOWED_SUPPORT_SUBDIRS))}.")
        return normalized_relative_path

    def read_skill_file(self, skill_name: str, category: SkillCategory | str, relative_path: str) -> str | bytes:
        skill_category = SkillCategory(category)
        normalized_name = self.validate_skill_name(skill_name)
        normalized_relative_path = self._normalize_skill_relative_path(normalized_name, skill_category, relative_path)
        with self._session_factory() as session:
            row = self._load_normalized_row(session, normalized_name, skill_category)
            if row is None:
                raise FileNotFoundError(f"{skill_category.value.title()} skill '{skill_name}' not found.")
            file_row = self._load_skill_file_row(session, skill_id=row.id, relative_path=normalized_relative_path)
            if file_row is None:
                raise FileNotFoundError(f"Skill file '{relative_path}' not found for {skill_category.value} skill '{skill_name}'.")
            return self._deserialize_support_file_content(file_row.content_json)

    def list_skill_file_manifest(self, skill_name: str, category: SkillCategory | str) -> list[SkillFileManifest]:
        skill_category = SkillCategory(category)
        normalized_name = self.validate_skill_name(skill_name)
        with self._session_factory() as session:
            row = self._load_normalized_row(session, normalized_name, skill_category)
            if row is None:
                raise FileNotFoundError(f"{skill_category.value.title()} skill '{skill_name}' not found.")
            return [
                SkillFileManifest(
                    relative_path=file_row.relative_path,
                    content_hash=file_row.content_hash,
                    mime_type=file_row.mime_type,
                    size=file_row.size,
                )
                for file_row in self._list_skill_file_rows(session, skill_id=row.id)
            ]

    def write_custom_skill(self, name: str, relative_path: str, content: str) -> None:
        self._write_custom_skill_payload(name, relative_path, content)

    def _write_custom_skill_payload(self, name: str, relative_path: str, content: str | bytes, *, mode: int | None = None) -> None:
        normalized_name = self.validate_skill_name(name)
        skill_dir = self._custom_skill_dir(normalized_name)
        target = self.validate_relative_path(relative_path, skill_dir)
        normalized_relative_path = target.relative_to(skill_dir).as_posix()

        with self._session_factory() as session:
            row = self._load_row(session, normalized_name, prune_duplicates=True)
            if row is None:
                if normalized_relative_path != SKILL_MD_FILE:
                    raise FileNotFoundError(f"Custom skill '{name}' not found.")
                if isinstance(content, bytes):
                    content = content.decode("utf-8")
                metadata_json = self._extract_metadata_from_skill_md(normalized_name, content) or {}
                row = SkillRow(
                    owner_user_id=self._owner_user_id,
                    skill_name=normalized_name,
                    skill_md_text=content,
                    metadata_json=metadata_json,
                    files_json={},
                    revision=1,
                )
                session.add(row)
            elif normalized_relative_path == SKILL_MD_FILE:
                if isinstance(content, bytes):
                    content = content.decode("utf-8")
                row.skill_md_text = content
                row.metadata_json = self._extract_metadata_from_skill_md(normalized_name, content) or {}
                row.revision += 1
            else:
                files = copy.deepcopy(row.files_json or {})
                if mode is None:
                    mode = self._payload_mode(files.get(normalized_relative_path))
                files[normalized_relative_path] = self._serialize_support_file_content(content, mode=mode)
                row.files_json = files
                row.revision += 1
            session.commit()
            session.refresh(row)
            self._materialize_row(row)
        self._upsert_normalized_from_legacy_row(row)

    async def ainstall_skill_from_archive(self, archive_path: str | Path) -> dict:
        import zipfile

        from deerflow.skills.installer import _scan_skill_archive_contents_or_raise, resolve_skill_dir_from_archive, safe_extract_skill_archive
        from deerflow.skills.validation import _validate_skill_frontmatter

        path = Path(archive_path)
        if not path.is_file():
            if not path.exists():
                raise FileNotFoundError(f"Skill file not found: {archive_path}")
            raise ValueError(f"Path is not a file: {archive_path}")
        if path.suffix != ".skill":
            raise ValueError("File must have .skill extension")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    archive_file_modes = self._read_archive_file_modes(zf)
                    safe_extract_skill_archive(zf, tmp_path)
            except FileNotFoundError:
                raise FileNotFoundError(f"Skill file not found: {archive_path}") from None
            except (zipfile.BadZipFile, IsADirectoryError):
                raise ValueError("File is not a valid ZIP archive") from None

            skill_dir = resolve_skill_dir_from_archive(tmp_path)
            is_valid, message, skill_name = _validate_skill_frontmatter(skill_dir)
            if not is_valid or not skill_name:
                raise ValueError(f"Invalid skill: {message}")
            skill_name = self.validate_skill_name(skill_name)
            if self.custom_skill_exists(skill_name):
                from deerflow.skills.installer import SkillAlreadyExistsError

                raise SkillAlreadyExistsError(f"Skill '{skill_name}' already exists")

            await _scan_skill_archive_contents_or_raise(skill_dir, skill_name)
            skill_md_text = (skill_dir / SKILL_MD_FILE).read_text(encoding="utf-8")
            self.write_custom_skill(skill_name, SKILL_MD_FILE, skill_md_text)
            archive_root = "" if skill_dir == tmp_path else skill_dir.relative_to(tmp_path).as_posix()
            for child in sorted(skill_dir.rglob("*")):
                if not child.is_file() or child.name == SKILL_MD_FILE:
                    continue
                relative_path = child.relative_to(skill_dir).as_posix()
                archive_member_path = posixpath.join(archive_root, relative_path) if archive_root else relative_path
                raw_content = child.read_bytes()
                try:
                    support_content: str | bytes = raw_content.decode("utf-8")
                except UnicodeDecodeError:
                    support_content = raw_content
                self._write_custom_skill_payload(
                    skill_name,
                    relative_path,
                    support_content,
                    mode=archive_file_modes.get(archive_member_path),
                )

        return {
            "success": True,
            "skill_name": skill_name,
            "message": f"Skill '{skill_name}' installed successfully",
        }

    def delete_custom_skill(self, name: str, *, history_meta: dict | None = None) -> None:
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        if history_meta is not None:
            prev_content = self.read_custom_skill(normalized_name)
            self.append_history(normalized_name, {**history_meta, "prev_content": prev_content})

        with self._session_factory() as session:
            rows = self._load_rows(session, normalized_name)
            for row in rows:
                session.delete(row)
            normalized_rows = self._load_normalized_rows(session, normalized_name, SkillCategory.CUSTOM)
            for normalized_row in normalized_rows:
                for file_row in self._list_all_skill_file_rows(session, skill_id=normalized_row.id):
                    session.delete(file_row)
                session.delete(normalized_row)
            if rows or normalized_rows:
                session.commit()
        shutil.rmtree(self._custom_skill_dir(normalized_name), ignore_errors=True)

    def delete_custom_skill_file(self, name: str, relative_path: str) -> None:
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        target = self.ensure_safe_support_path(normalized_name, relative_path)
        normalized_relative_path = target.relative_to(self._custom_skill_dir(normalized_name)).as_posix()

        with self._session_factory() as session:
            row = self._load_row(session, normalized_name, prune_duplicates=True)
            if row is None:
                raise FileNotFoundError(f"Custom skill '{name}' not found.")
            files = copy.deepcopy(row.files_json or {})
            if normalized_relative_path not in files:
                raise FileNotFoundError(f"Supporting file '{relative_path}' not found for skill '{name}'.")
            del files[normalized_relative_path]
            row.files_json = files
            row.revision += 1
            session.commit()
            session.refresh(row)
            self._upsert_normalized_from_legacy_row(row)

        if target.exists():
            target.unlink()

    def custom_skill_exists(self, name: str) -> bool:
        normalized_name = self.validate_skill_name(name)
        return self._load_normalized_row_by_name(normalized_name, SkillCategory.CUSTOM) is not None

    def public_skill_exists(self, name: str) -> bool:
        normalized_name = self.validate_skill_name(name)
        return self._load_normalized_row_by_name(normalized_name, SkillCategory.PUBLIC) is not None

    def seed_public_skills_from_root(self, skills_root: str | Path) -> int:
        public_root = Path(skills_root) / SkillCategory.PUBLIC.value
        if not public_root.is_dir():
            return 0
        imported = 0
        for skill_dir in sorted(path for path in public_root.iterdir() if path.is_dir()):
            if (skill_dir / SKILL_MD_FILE).is_file():
                self.seed_public_skill_from_directory(skill_dir)
                imported += 1
        return imported

    def seed_public_skill_from_directory(self, skill_dir: str | Path) -> None:
        import stat

        from deerflow.skills.validation import _validate_skill_frontmatter

        source_dir = Path(skill_dir)
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Public skill directory not found: {source_dir}")
        is_valid, message, skill_name = _validate_skill_frontmatter(source_dir)
        if not is_valid or not skill_name:
            raise ValueError(f"Invalid public skill: {message}")
        normalized_name = self.validate_skill_name(skill_name)
        if source_dir.name != normalized_name:
            raise ValueError(f"Public skill frontmatter name {normalized_name!r} does not match directory name {source_dir.name!r}")

        skill_md_text = (source_dir / SKILL_MD_FILE).read_text(encoding="utf-8")
        metadata = self._extract_metadata_from_skill_md(
            normalized_name,
            skill_md_text,
            category=SkillCategory.PUBLIC,
        )
        support_files: dict[str, Any] = {}
        for child in sorted(source_dir.rglob("*")):
            if not child.is_file() or child.name == SKILL_MD_FILE:
                continue
            relative_path = child.relative_to(source_dir).as_posix()
            self._normalize_skill_relative_path(normalized_name, SkillCategory.PUBLIC, relative_path)
            raw_content = child.read_bytes()
            try:
                support_content: str | bytes = raw_content.decode("utf-8")
            except UnicodeDecodeError:
                support_content = raw_content
            support_files[relative_path] = self._serialize_support_file_content(
                support_content,
                mode=stat.S_IMODE(child.stat().st_mode),
            )

        with self._session_factory() as session:
            self._upsert_normalized_skill(
                session,
                category=SkillCategory.PUBLIC,
                name=normalized_name,
                skill_md_text=skill_md_text,
                support_files=support_files,
                metadata_json=metadata or {},
                revision=1,
                source="public-seed",
            )
            session.commit()

    def append_history(self, name: str, record: dict) -> None:
        normalized_name = self.validate_skill_name(name)
        payload = {"ts": datetime.now(UTC).isoformat(), **record}
        with self._session_factory() as session:
            session.add(
                SkillHistoryRow(
                    owner_user_id=self._owner_user_id,
                    skill_name=normalized_name,
                    record_json=payload,
                )
            )
            session.commit()
        self.get_skill_history_file(normalized_name)

    def read_history(self, name: str) -> list[dict]:
        normalized_name = self.validate_skill_name(name)
        stmt = (
            select(SkillHistoryRow)
            .where(
                SkillHistoryRow.owner_user_id == self._owner_user_id,
                SkillHistoryRow.skill_name == normalized_name,
            )
            .order_by(SkillHistoryRow.id.asc())
        )
        with self._session_factory() as session:
            return [copy.deepcopy(row.record_json) for row in session.execute(stmt).scalars()]
