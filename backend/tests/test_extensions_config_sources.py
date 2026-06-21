from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from deerflow.config.database_config import DatabaseConfig
from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.extensions_sources import DbExtensionsConfigSource, DbExtensionsConfigStore, ExtensionsConfigConflictError, FileExtensionsConfigSource, RevisionedExtensionsConfig
from deerflow.config.mcp_store import DbMcpServerStore
from deerflow.persistence.base import Base
from deerflow.persistence.runtime_config.model import RuntimeConfigRow
from deerflow.persistence.runtime_config.sql import RuntimeConfigRepository, stable_json_hash

pytestmark = pytest.mark.anyio


async def _repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'extensions-source.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return RuntimeConfigRepository(async_sessionmaker(engine, expire_on_commit=False)), engine


async def test_file_extensions_config_source_loads_json(tmp_path) -> None:
    extensions_path = tmp_path / "extensions_config.json"
    payload = {
        "mcpServers": {"demo": {"type": "http", "url": "https://example.test/mcp"}},
        "skills": {"writer": {"enabled": False}},
    }
    extensions_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = FileExtensionsConfigSource(str(extensions_path)).load_extensions_config()

    assert isinstance(loaded, RevisionedExtensionsConfig)
    assert "demo" in loaded.config.mcp_servers
    assert loaded.config.skills["writer"].enabled is False
    assert loaded.revision > 0
    assert loaded.content_hash == stable_json_hash(payload)
    assert isinstance(loaded.updated_at, datetime)


async def test_db_extensions_config_source_loads_runtime_config_key(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        payload = {
            "mcpServers": {"demo": {"type": "http", "url": "https://example.test/mcp"}},
            "skills": {"writer": {"enabled": False}},
        }
        await repo.upsert("extensions", payload)

        loaded = await DbExtensionsConfigSource(repo).load_extensions_config()

        assert "demo" in loaded.config.mcp_servers
        assert loaded.config.skills["writer"].enabled is False
        assert loaded.revision == 1
        assert loaded.content_hash == stable_json_hash(payload)
    finally:
        await engine.dispose()


async def test_db_extensions_config_source_missing_key_returns_empty_config(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        loaded = await DbExtensionsConfigSource(repo).load_extensions_config()

        assert loaded.config.mcp_servers == {}
        assert loaded.config.skills == {}
        assert loaded.revision == 0
        assert loaded.content_hash == stable_json_hash({"mcpServers": {}, "skills": {}})
    finally:
        await engine.dispose()


async def test_db_extensions_config_store_round_trips_runtime_config_key(tmp_path) -> None:
    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    store = DbExtensionsConfigStore(database_config=database)
    config = ExtensionsConfig.model_validate(
        {
            "mcpServers": {"demo": {"type": "http", "url": "https://example.test/mcp"}},
            "skills": {"writer": {"enabled": False}},
            "mcpInterceptors": ["pkg.module:build"],
        }
    )

    saved = store.save_extensions_config(config, updated_by="tester")
    loaded = store.load_extensions_config()

    assert saved.revision == 1
    assert loaded.config.mcp_servers["demo"].url == "https://example.test/mcp"
    assert loaded.config.skills["writer"].enabled is False
    assert loaded.config.model_extra["mcpInterceptors"] == ["pkg.module:build"]
    assert loaded.updated_by == "tester"


async def test_db_extensions_config_store_writes_mcp_servers_to_dedicated_rows(tmp_path) -> None:
    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    store = DbExtensionsConfigStore(database_config=database)
    config = ExtensionsConfig.model_validate(
        {
            "mcpServers": {
                "demo": {
                    "type": "http",
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                }
            },
            "skills": {"writer": {"enabled": False}},
            "mcpInterceptors": ["pkg.module:build"],
        }
    )

    saved = store.save_extensions_config(config, updated_by="tester")

    mcp_loaded = DbMcpServerStore(database_config=database).load_extensions_config()
    assert mcp_loaded.config.mcp_servers["demo"].headers == {"Authorization": "Bearer secret"}

    loaded = store.load_extensions_config()
    assert loaded.revision == saved.revision
    assert loaded.config.mcp_servers["demo"].url == "https://example.test/mcp"
    assert loaded.config.skills["writer"].enabled is False
    assert loaded.config.model_extra["mcpInterceptors"] == ["pkg.module:build"]

    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with Session(engine) as session:
        row = session.get(RuntimeConfigRow, "extensions")
        assert row is not None
        assert "mcpServers" not in row.payload_json
        assert row.payload_json["skills"] == {"writer": {"enabled": False}}
        assert row.payload_json["mcpInterceptors"] == ["pkg.module:build"]


async def test_db_extensions_config_store_migrates_legacy_aggregate_mcp_servers(tmp_path) -> None:
    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    (tmp_path / "db").mkdir(parents=True)
    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    Base.metadata.create_all(engine)
    legacy_payload = {
        "mcpServers": {
            "github": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
            }
        },
        "skills": {"writer": {"enabled": False}},
        "mcpInterceptors": ["pkg.module:build"],
    }
    with Session(engine) as session:
        session.add(
            RuntimeConfigRow(
                key="extensions",
                payload_json=legacy_payload,
                schema_version="1",
                revision=3,
                content_hash=stable_json_hash(legacy_payload),
                updated_by="legacy-import",
            )
        )
        session.commit()

    loaded = DbExtensionsConfigStore(database_config=database).load_extensions_config()

    assert loaded.config.mcp_servers["github"].command == "npx"
    assert loaded.config.mcp_servers["github"].env == {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    assert loaded.config.skills["writer"].enabled is False
    assert loaded.config.model_extra["mcpInterceptors"] == ["pkg.module:build"]

    mcp_loaded = DbMcpServerStore(database_config=database).load_extensions_config()
    assert mcp_loaded.config.mcp_servers["github"].args == ["-y", "@modelcontextprotocol/server-github"]
    assert mcp_loaded.updated_by == "legacy-import"

    with Session(engine) as session:
        row = session.get(RuntimeConfigRow, "extensions")
        assert row is not None
        assert row.revision == 4
        assert row.updated_by == "legacy-import"
        assert "mcpServers" not in row.payload_json
        assert row.payload_json["skills"] == {"writer": {"enabled": False}}
        assert row.payload_json["mcpInterceptors"] == ["pkg.module:build"]
        assert row.content_hash == stable_json_hash(row.payload_json)


async def test_db_extensions_config_store_rejects_stale_expected_revision(tmp_path) -> None:
    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    first = DbExtensionsConfigStore(database_config=database)
    second = DbExtensionsConfigStore(database_config=database)

    saved = first.save_extensions_config(
        ExtensionsConfig.model_validate(
            {
                "mcpServers": {"github": {"type": "stdio", "command": "npx"}},
                "skills": {},
            }
        )
    )
    second.save_extensions_config(
        ExtensionsConfig.model_validate(
            {
                "mcpServers": {"linear": {"type": "http", "url": "https://linear.example/mcp"}},
                "skills": {},
            }
        ),
        expected_revision=saved.revision,
    )

    with pytest.raises(ExtensionsConfigConflictError):
        first.save_extensions_config(
            ExtensionsConfig.model_validate(
                {
                    "mcpServers": {"github": {"type": "stdio", "command": "uvx"}},
                    "skills": {},
                }
            ),
            expected_revision=saved.revision,
        )

    loaded = first.load_extensions_config()
    assert loaded.revision == saved.revision + 1
    assert "linear" in loaded.config.mcp_servers
    assert "github" not in loaded.config.mcp_servers


async def test_db_extensions_config_cache_refreshes_when_revision_changes(tmp_path, monkeypatch) -> None:
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.config.extensions_config import get_extensions_config, reset_extensions_config

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    set_app_config(
        AppConfig.model_validate(
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "database": database.model_dump(),
            }
        )
    )
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    store = DbExtensionsConfigStore(database_config=database)
    first = store.save_extensions_config(
        ExtensionsConfig.model_validate(
            {
                "mcpServers": {"github": {"type": "stdio", "command": "npx"}},
                "skills": {},
            }
        )
    )
    reset_extensions_config()

    try:
        assert "github" in get_extensions_config().mcp_servers
        store.save_extensions_config(
            ExtensionsConfig.model_validate(
                {
                    "mcpServers": {"linear": {"type": "http", "url": "https://linear.example/mcp"}},
                    "skills": {},
                }
            ),
            expected_revision=first.revision,
        )

        refreshed = get_extensions_config()
    finally:
        reset_app_config()
        reset_extensions_config()

    assert "linear" in refreshed.mcp_servers
    assert "github" not in refreshed.mcp_servers


async def test_db_extensions_config_cache_refreshes_after_direct_mcp_store_write(tmp_path, monkeypatch) -> None:
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.config.extensions_config import get_extensions_config, reset_extensions_config

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    set_app_config(
        AppConfig.model_validate(
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "database": database.model_dump(),
            }
        )
    )
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    store = DbExtensionsConfigStore(database_config=database)
    store.save_extensions_config(
        ExtensionsConfig.model_validate(
            {
                "mcpServers": {"github": {"type": "stdio", "command": "npx"}},
                "skills": {},
            }
        )
    )
    reset_extensions_config()

    try:
        assert "github" in get_extensions_config().mcp_servers
        DbMcpServerStore(database_config=database).save_mcp_servers(
            {
                "linear": {
                    "type": "http",
                    "url": "https://linear.example/mcp",
                }
            },
            updated_by="mcp-admin",
        )

        refreshed = get_extensions_config()
    finally:
        reset_app_config()
        reset_extensions_config()

    assert "linear" in refreshed.mcp_servers
    assert "github" not in refreshed.mcp_servers
