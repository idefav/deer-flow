from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.config.bootstrap import (
    get_bootstrap_database_config,
    get_bootstrap_database_url,
    get_config_source_mode,
    is_db_config_enabled,
)
from deerflow.persistence.base import Base
from deerflow.persistence.runtime_config.sql import RuntimeConfigRepository, stable_json_hash

pytestmark = pytest.mark.anyio


async def _repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime-config.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return RuntimeConfigRepository(async_sessionmaker(engine, expire_on_commit=False)), engine


def test_stable_json_hash_is_order_insensitive() -> None:
    assert stable_json_hash({"b": 2, "a": {"z": 1, "y": [3, 4]}}) == stable_json_hash(
        {"a": {"y": [3, 4], "z": 1}, "b": 2}
    )


async def test_runtime_config_upsert_insert_then_revision_increment(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        inserted = await repo.upsert("app", {"database": {"backend": "sqlite"}}, updated_by="alice")

        assert inserted.key == "app"
        assert inserted.payload == {"database": {"backend": "sqlite"}}
        assert inserted.revision == 1
        assert inserted.content_hash == stable_json_hash({"database": {"backend": "sqlite"}})
        assert inserted.updated_by == "alice"

        updated = await repo.upsert("app", {"database": {"backend": "postgres"}}, updated_by="bob")

        assert updated.revision == 2
        assert updated.payload == {"database": {"backend": "postgres"}}
        assert updated.content_hash == stable_json_hash({"database": {"backend": "postgres"}})
        assert updated.updated_by == "bob"
    finally:
        await engine.dispose()


async def test_runtime_config_upsert_reports_startup_only_changes(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        await repo.upsert(
            "app",
            {
                "database": {"backend": "sqlite"},
                "memory": {"enabled": True},
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            },
        )

        updated = await repo.upsert(
            "app",
            {
                "database": {"backend": "postgres"},
                "memory": {"enabled": False},
                "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
            },
        )

        assert updated.changed_fields == ("database", "memory", "sandbox")
        assert updated.restart_required_fields == ("database", "sandbox")
        assert updated.requires_restart is True
        assert "SQLAlchemy engine" in updated.restart_required_reasons["database"]
        assert "sandbox" in updated.restart_required_reasons["sandbox"].lower()
    finally:
        await engine.dispose()


async def test_runtime_config_upsert_ignores_non_startup_changes_for_restart_metadata(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        await repo.upsert("app", {"memory": {"enabled": True}})

        updated = await repo.upsert("app", {"memory": {"enabled": False}})

        assert updated.changed_fields == ("memory",)
        assert updated.restart_required_fields == ()
        assert updated.requires_restart is False
        assert updated.restart_required_reasons == {}
    finally:
        await engine.dispose()


async def test_runtime_config_load_returns_independent_payload_copy(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        source_payload = {"memory": {"enabled": True}}
        await repo.upsert("app", source_payload)
        source_payload["memory"]["enabled"] = False

        loaded = await repo.load("app")
        assert loaded is not None
        assert loaded.payload == {"memory": {"enabled": True}}

        loaded.payload["memory"]["enabled"] = "changed"
        reloaded = await repo.load("app")
        assert reloaded is not None
        assert reloaded.payload == {"memory": {"enabled": True}}
    finally:
        await engine.dispose()


async def test_runtime_config_load_missing_returns_none(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        assert await repo.load("missing") is None
    finally:
        await engine.dispose()


def test_bootstrap_config_source_mode_defaults_to_file(monkeypatch) -> None:
    monkeypatch.delenv("DEER_FLOW_CONFIG_SOURCE", raising=False)

    assert get_config_source_mode() == "file"
    assert not is_db_config_enabled()


def test_bootstrap_config_source_mode_accepts_db(monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")

    assert get_config_source_mode() == "db"
    assert is_db_config_enabled()


def test_bootstrap_database_url_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", "sqlite+aiosqlite:////tmp/deerflow.db")

    assert get_bootstrap_database_url() == "sqlite+aiosqlite:////tmp/deerflow.db"


def test_bootstrap_database_config_from_sqlite_url(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "deerflow.db"
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    config = get_bootstrap_database_config()

    assert config is not None
    assert config.backend == "sqlite"
    assert config.sqlite_dir == str(tmp_path)
    assert config.app_sqlalchemy_url == f"sqlite+aiosqlite:///{db_path}"


def test_bootstrap_database_config_from_postgres_url(monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", "postgresql://user:pass@localhost:5432/deerflow")

    config = get_bootstrap_database_config()

    assert config is not None
    assert config.backend == "postgres"
    assert config.app_sqlalchemy_url == "postgresql+asyncpg://user:pass@localhost:5432/deerflow"
