from __future__ import annotations

from datetime import datetime

import pytest
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.config.app_config import AppConfig, get_app_config, load_and_cache_db_app_config, reset_app_config
from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.memory_config import get_memory_config
from deerflow.config.sources import DbConfigSource, FileConfigSource, RevisionedPayload
from deerflow.persistence.base import Base
from deerflow.persistence.runtime_config.sql import RuntimeConfigRepository, stable_json_hash

pytestmark = pytest.mark.anyio


async def _repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'config-source.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return RuntimeConfigRepository(async_sessionmaker(engine, expire_on_commit=False)), engine


async def test_file_config_source_loads_yaml_as_revisioned_payload(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    payload = {
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        "models": [{"name": "basic", "use": "langchain_openai:ChatOpenAI", "model": "gpt-test"}],
    }
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    loaded = FileConfigSource(str(config_path)).load_app_config_payload()

    assert isinstance(loaded, RevisionedPayload)
    assert loaded.payload == payload
    assert loaded.revision > 0
    assert loaded.content_hash == stable_json_hash(payload)
    assert isinstance(loaded.updated_at, datetime)


async def test_file_config_source_returns_independent_payload_copy(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"memory": {"enabled": True}}), encoding="utf-8")

    source = FileConfigSource(str(config_path))
    first = source.load_app_config_payload()
    first.payload["memory"]["enabled"] = False

    second = source.load_app_config_payload()

    assert second.payload == {"memory": {"enabled": True}}


async def test_db_config_source_loads_app_runtime_config(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        await repo.upsert("app", {"memory": {"enabled": True}}, updated_by="tester")

        loaded = await DbConfigSource(repo).load_app_config_payload()

        assert loaded.payload == {"memory": {"enabled": True}}
        assert loaded.revision == 1
        assert loaded.content_hash == stable_json_hash({"memory": {"enabled": True}})
        assert loaded.updated_by == "tester"
    finally:
        await engine.dispose()


async def test_db_config_source_missing_app_raises_key_error(tmp_path) -> None:
    repo, engine = await _repo(tmp_path)
    try:
        with pytest.raises(KeyError, match="runtime config key 'app'"):
            await DbConfigSource(repo).load_app_config_payload()
    finally:
        await engine.dispose()


def test_app_config_from_payload_applies_existing_file_loader_semantics(monkeypatch) -> None:
    monkeypatch.setenv("TEST_MODEL_NAME", "gpt-from-env")
    payload = {
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        "models": [
            {
                "name": "basic",
                "use": "langchain_openai:ChatOpenAI",
                "model": "$TEST_MODEL_NAME",
            }
        ],
        "memory": {"enabled": False},
    }

    config = AppConfig.from_payload(
        payload,
        extensions_config=ExtensionsConfig.model_validate(
            {"mcpServers": {"demo": {"type": "http", "url": "https://example.test/mcp"}}}
        ),
    )

    assert config.models[0].model == "gpt-from-env"
    assert config.database.backend == "sqlite"
    assert "database" not in payload
    assert "demo" in config.extensions.mcp_servers
    assert get_memory_config().enabled is False


def test_app_config_from_payload_loads_strict_sandbox_runtime_context_flag() -> None:
    config = AppConfig.from_payload(
        {
            "sandbox": {
                "use": "deerflow.sandbox.local:LocalSandboxProvider",
                "runtime_context_fail_closed": True,
            },
        },
        extensions_config=ExtensionsConfig(),
        apply_singletons=False,
    )

    assert config.sandbox.runtime_context_fail_closed is True


def test_app_config_from_source_validates_file_source_payload(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "models": [
                    {
                        "name": "basic",
                        "use": "langchain_openai:ChatOpenAI",
                        "model": "gpt-test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig.from_source(FileConfigSource(str(config_path)), extensions_config=ExtensionsConfig())

    assert config.models[0].name == "basic"
    assert config.database.backend == "sqlite"


async def test_get_app_config_db_mode_requires_cached_startup_load(monkeypatch) -> None:
    reset_app_config()
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    try:
        with pytest.raises(RuntimeError, match="load_and_cache_db_app_config"):
            get_app_config()
    finally:
        reset_app_config()


async def test_load_and_cache_db_app_config_feeds_sync_get_app_config(tmp_path, monkeypatch) -> None:
    repo, engine = await _repo(tmp_path)
    reset_app_config()
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    try:
        await repo.upsert(
            "app",
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "models": [
                    {
                        "name": "db-model",
                        "use": "langchain_openai:ChatOpenAI",
                        "model": "gpt-db",
                    }
                ],
            },
        )

        config = await load_and_cache_db_app_config(repo, extensions_config=ExtensionsConfig())

        assert config.models[0].name == "db-model"
        assert get_app_config() is config
    finally:
        reset_app_config()
        await engine.dispose()


async def test_load_and_cache_db_app_config_refreshes_cached_revision(tmp_path, monkeypatch) -> None:
    repo, engine = await _repo(tmp_path)
    reset_app_config()
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    try:
        await repo.upsert(
            "app",
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "models": [{"name": "first", "use": "langchain_openai:ChatOpenAI", "model": "gpt-a"}],
            },
        )
        first = await load_and_cache_db_app_config(repo, extensions_config=ExtensionsConfig())

        await repo.upsert(
            "app",
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "models": [{"name": "second", "use": "langchain_openai:ChatOpenAI", "model": "gpt-b"}],
            },
        )
        second = await load_and_cache_db_app_config(repo, extensions_config=ExtensionsConfig())

        assert first.models[0].name == "first"
        assert second.models[0].name == "second"
        assert get_app_config() is second
    finally:
        reset_app_config()
        await engine.dispose()
