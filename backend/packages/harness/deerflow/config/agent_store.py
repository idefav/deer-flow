"""DB-backed custom agent store."""

from __future__ import annotations

import copy
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from deerflow.config.agents_config import AgentConfig, validate_agent_name
from deerflow.config.app_config import get_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.agents.model import CustomAgentRow, DefaultAgentSoulRow, UserProfileRow
from deerflow.persistence.base import Base


class DefaultAgentSoulStoreConflictError(RuntimeError):
    """Raised when a default-agent SOUL save sees a stale revision."""


class DbAgentStore:
    """Store custom agents and user profiles in the application database."""

    def __init__(self, *, database_config: DatabaseConfig | None = None) -> None:
        self._database_config = database_config or get_app_config().database
        self._engine = create_engine(self._sync_sqlalchemy_url(self._database_config))
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
        raise ValueError("DbAgentStore requires sqlite or postgres database backend")

    @staticmethod
    def _row_to_config(row: CustomAgentRow) -> AgentConfig:
        return AgentConfig.model_validate(copy.deepcopy(row.config_json))

    def _load_rows(self, session: Session, owner_user_id: str, agent_name: str) -> list[CustomAgentRow]:
        stmt = select(CustomAgentRow).where(
            CustomAgentRow.owner_user_id == owner_user_id,
            CustomAgentRow.agent_name == agent_name,
        ).order_by(
            CustomAgentRow.updated_at.desc(),
            CustomAgentRow.revision.desc(),
            CustomAgentRow.id.desc(),
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def _prune_duplicate_agent_rows(session: Session, rows: list[CustomAgentRow]) -> CustomAgentRow | None:
        if not rows:
            return None
        for duplicate in rows[1:]:
            session.delete(duplicate)
        return rows[0]

    def _load_row(
        self,
        session: Session,
        owner_user_id: str,
        agent_name: str,
        *,
        prune_duplicates: bool = False,
    ) -> CustomAgentRow | None:
        rows = self._load_rows(session, owner_user_id, agent_name)
        if prune_duplicates:
            return self._prune_duplicate_agent_rows(session, rows)
        return rows[0] if rows else None

    def load_agent_config(self, owner_user_id: str, agent_name: str) -> AgentConfig | None:
        agent_name = validate_agent_name(agent_name)
        with self._session_factory() as session:
            row = self._load_row(session, owner_user_id, agent_name)
            if row is None:
                return None
            return self._row_to_config(row)

    def load_agent_soul(self, owner_user_id: str, agent_name: str) -> str | None:
        agent_name = validate_agent_name(agent_name)
        with self._session_factory() as session:
            row = self._load_row(session, owner_user_id, agent_name)
            if row is None:
                return None
            return row.soul_text or None

    def list_agents(self, owner_user_id: str) -> list[AgentConfig]:
        stmt = select(CustomAgentRow).where(CustomAgentRow.owner_user_id == owner_user_id).order_by(
            CustomAgentRow.agent_name.asc(),
            CustomAgentRow.updated_at.desc(),
            CustomAgentRow.revision.desc(),
            CustomAgentRow.id.desc(),
        )
        with self._session_factory() as session:
            rows_by_name: dict[str, CustomAgentRow] = {}
            for row in session.execute(stmt).scalars():
                rows_by_name.setdefault(row.agent_name, row)
            return [self._row_to_config(row) for row in rows_by_name.values()]

    def save_agent(self, owner_user_id: str, agent_name: str, config: AgentConfig | dict, soul: str) -> AgentConfig:
        agent_name = validate_agent_name(agent_name)
        agent_config = config if isinstance(config, AgentConfig) else AgentConfig.model_validate(config)
        config_json = agent_config.model_dump()
        config_json["name"] = agent_name
        with self._session_factory() as session:
            row = self._load_row(session, owner_user_id, agent_name, prune_duplicates=True)
            if row is None:
                row = CustomAgentRow(
                    owner_user_id=owner_user_id,
                    agent_name=agent_name,
                    config_json=config_json,
                    soul_text=soul,
                    revision=1,
                )
                session.add(row)
            else:
                row.config_json = config_json
                row.soul_text = soul
                row.revision += 1
            session.commit()
            session.refresh(row)
            return self._row_to_config(row)

    def delete_agent(self, owner_user_id: str, agent_name: str) -> bool:
        agent_name = validate_agent_name(agent_name)
        with self._session_factory() as session:
            rows = self._load_rows(session, owner_user_id, agent_name)
            if not rows:
                return False
            for row in rows:
                session.delete(row)
            session.commit()
            return True

    def load_user_profile(self, owner_user_id: str) -> str | None:
        with self._session_factory() as session:
            row = session.get(UserProfileRow, owner_user_id)
            if row is None:
                return None
            return row.profile_text

    def save_user_profile(self, owner_user_id: str, profile_text: str) -> str:
        with self._session_factory() as session:
            row = session.get(UserProfileRow, owner_user_id)
            if row is None:
                row = UserProfileRow(owner_user_id=owner_user_id, profile_text=profile_text, revision=1)
                session.add(row)
            else:
                row.profile_text = profile_text
                row.revision += 1
            session.commit()
            session.refresh(row)
            return row.profile_text

    def load_default_agent_soul(self, owner_user_id: str) -> str | None:
        with self._session_factory() as session:
            row = session.get(DefaultAgentSoulRow, owner_user_id)
            if row is None:
                return None
            return row.soul_text or None

    def load_default_agent_soul_state(self, owner_user_id: str) -> tuple[str | None, int]:
        with self._session_factory() as session:
            row = session.get(DefaultAgentSoulRow, owner_user_id)
            if row is None:
                return None, 0
            return row.soul_text or None, row.revision

    def save_default_agent_soul(self, owner_user_id: str, soul_text: str, *, expected_revision: int | None = None) -> str:
        with self._session_factory() as session:
            row = session.get(DefaultAgentSoulRow, owner_user_id)
            if row is None:
                if expected_revision not in (None, 0):
                    raise DefaultAgentSoulStoreConflictError(
                        f"Default-agent SOUL revision conflict: expected {expected_revision}, found missing row"
                    )
                row = DefaultAgentSoulRow(owner_user_id=owner_user_id, soul_text=soul_text, revision=1)
                session.add(row)
            else:
                if expected_revision is not None and row.revision != expected_revision:
                    raise DefaultAgentSoulStoreConflictError(
                        f"Default-agent SOUL revision conflict: expected {expected_revision}, found {row.revision}"
                    )
                row.soul_text = soul_text
                row.revision += 1
            session.commit()
            session.refresh(row)
            return row.soul_text
