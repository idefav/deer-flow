from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.app import _load_startup_config
from deerflow.config.app_config import get_app_config, reset_app_config
from deerflow.persistence.base import Base
from deerflow.persistence.engine import close_engine
from deerflow.persistence.runtime_config.sql import RuntimeConfigRepository

pytestmark = pytest.mark.anyio


async def _seed_runtime_config(db_url: str) -> None:
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = RuntimeConfigRepository(async_sessionmaker(engine, expire_on_commit=False))
    await repo.upsert(
        "app",
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [
                {
                    "name": "db-startup",
                    "use": "langchain_openai:ChatOpenAI",
                    "model": "gpt-db",
                }
            ],
            "database": {"backend": "memory"},
        },
    )
    await engine.dispose()


async def _seed_runtime_extensions(db_url: str) -> None:
    engine = create_async_engine(db_url)
    repo = RuntimeConfigRepository(async_sessionmaker(engine, expire_on_commit=False))
    await repo.upsert(
        "extensions",
        {
            "mcpServers": {"demo": {"type": "http", "url": "https://example.test/mcp"}},
            "skills": {"writer": {"enabled": False}},
        },
    )
    await engine.dispose()


async def test_load_startup_config_db_mode_preloads_from_bootstrap_db(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "deerflow.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    extensions_path = tmp_path / "extensions_config.json"
    extensions_path.write_text(json.dumps({"mcpServers": {}, "skills": {}}), encoding="utf-8")
    await _seed_runtime_config(db_url)

    reset_app_config()
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", db_url)
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))
    try:
        config = await _load_startup_config()

        assert config.models[0].name == "db-startup"
        assert config.database.backend == "sqlite"
        assert config.database.sqlite_dir == str(tmp_path)
        assert get_app_config() is config
    finally:
        reset_app_config()
        await close_engine()


async def test_load_startup_config_db_mode_loads_extensions_from_db(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "deerflow.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    await _seed_runtime_config(db_url)
    await _seed_runtime_extensions(db_url)

    reset_app_config()
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", db_url)
    try:
        config = await _load_startup_config()

        assert "demo" in config.extensions.mcp_servers
        assert config.extensions.skills["writer"].enabled is False
    finally:
        reset_app_config()
        await close_engine()
