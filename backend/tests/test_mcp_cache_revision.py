from __future__ import annotations

from langchain_core.tools import StructuredTool

from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.extensions_config import ExtensionsConfig, reset_extensions_config
from deerflow.config.extensions_sources import DbExtensionsConfigStore


def test_mcp_tools_cache_is_stale_when_db_extensions_revision_changes(tmp_path, monkeypatch) -> None:
    from deerflow.mcp import cache as mcp_cache

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
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", f"sqlite:///{database.sqlite_path}")
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
        mcp_cache._cache_initialized = True
        mcp_cache._mcp_tools_cache = []
        mcp_cache._config_mtime = None
        mcp_cache._config_revision = first.revision

        store.save_extensions_config(
            ExtensionsConfig.model_validate(
                {
                    "mcpServers": {"linear": {"type": "http", "url": "https://linear.example/mcp"}},
                    "skills": {},
                }
            ),
            expected_revision=first.revision,
        )

        assert mcp_cache._is_cache_stale() is True
    finally:
        mcp_cache.reset_mcp_tools_cache()
        reset_app_config()
        reset_extensions_config()


def test_db_mcp_cache_stale_check_ignores_missing_file_config_path(tmp_path, monkeypatch) -> None:
    from deerflow.mcp import cache as mcp_cache

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
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", f"sqlite:///{database.sqlite_path}")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(tmp_path / "missing-extensions.json"))
    store = DbExtensionsConfigStore(database_config=database)
    current = store.save_extensions_config(
        ExtensionsConfig.model_validate(
            {
                "mcpServers": {"github": {"type": "stdio", "command": "npx"}},
                "skills": {},
            }
        )
    )
    reset_extensions_config()

    try:
        mcp_cache._cache_initialized = True
        mcp_cache._mcp_tools_cache = []
        mcp_cache._config_mtime = None
        mcp_cache._config_revision = current.revision

        assert mcp_cache._is_cache_stale() is False
    finally:
        mcp_cache.reset_mcp_tools_cache()
        reset_app_config()
        reset_extensions_config()


def test_get_cached_mcp_tools_reloads_when_db_extensions_revision_changes(tmp_path, monkeypatch) -> None:
    from deerflow.mcp import cache as mcp_cache

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
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", f"sqlite:///{database.sqlite_path}")
    store = DbExtensionsConfigStore(database_config=database)
    first = store.save_extensions_config(
        ExtensionsConfig.model_validate(
            {
                "mcpServers": {"github": {"type": "stdio", "command": "npx"}},
                "skills": {},
            }
        )
    )
    loaded_revisions: list[int | None] = []

    async def fake_get_mcp_tools():
        from deerflow.config.extensions_config import get_extensions_config_revision

        revision = get_extensions_config_revision()
        loaded_revisions.append(revision)

        def call() -> str:
            return str(revision)

        return [
            StructuredTool.from_function(
                call,
                name=f"server_tool_rev_{revision}",
                description="revision sentinel",
            )
        ]

    monkeypatch.setattr("deerflow.mcp.tools.get_mcp_tools", fake_get_mcp_tools)
    reset_extensions_config()

    try:
        first_tools = mcp_cache.get_cached_mcp_tools()

        second = store.save_extensions_config(
            ExtensionsConfig.model_validate(
                {
                    "mcpServers": {"linear": {"type": "http", "url": "https://linear.example/mcp"}},
                    "skills": {},
                }
            ),
            expected_revision=first.revision,
        )

        second_tools = mcp_cache.get_cached_mcp_tools()

        assert [tool.name for tool in first_tools] == [f"server_tool_rev_{first.revision}"]
        assert [tool.name for tool in second_tools] == [f"server_tool_rev_{second.revision}"]
        assert loaded_revisions == [first.revision, second.revision]
    finally:
        mcp_cache.reset_mcp_tools_cache()
        reset_app_config()
        reset_extensions_config()
