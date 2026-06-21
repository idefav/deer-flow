from __future__ import annotations

from pathlib import Path

import pytest

from deerflow.config.database_config import DatabaseConfig
from deerflow.config.extensions_config import McpServerConfig


def _db_config(tmp_path: Path) -> DatabaseConfig:
    return DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))


def test_db_mcp_store_loads_extensions_view_and_preserves_secret_payloads(tmp_path) -> None:
    from deerflow.config.mcp_store import DbMcpServerStore

    store = DbMcpServerStore(database_config=_db_config(tmp_path))
    saved = store.save_mcp_servers(
        {
            "github": McpServerConfig.model_validate(
                {
                    "enabled": True,
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "real-secret"},
                    "headers": {"Authorization": "Bearer real-token"},
                    "oauth": {
                        "token_url": "https://auth.example.com/token",
                        "client_id": "client-1",
                        "client_secret": "oauth-secret",
                        "refresh_token": "refresh-secret",
                    },
                    "description": "GitHub tools",
                    "customOption": {"mode": "strict"},
                }
            )
        },
        updated_by="admin-1",
    )

    loaded = store.load_extensions_config()
    server = loaded.config.mcp_servers["github"]

    assert loaded.revision == saved.revision
    assert loaded.content_hash == saved.content_hash
    assert loaded.updated_by == "admin-1"
    assert server.type == "stdio"
    assert server.command == "npx"
    assert server.args == ["-y", "@modelcontextprotocol/server-github"]
    assert server.env == {"GITHUB_TOKEN": "real-secret"}
    assert server.headers == {"Authorization": "Bearer real-token"}
    assert server.oauth is not None
    assert server.oauth.client_secret == "oauth-secret"
    assert server.oauth.refresh_token == "refresh-secret"
    assert server.description == "GitHub tools"
    assert server.model_extra["customOption"] == {"mode": "strict"}
    assert loaded.config.skills == {}


def test_db_mcp_store_rejects_stale_full_save(tmp_path) -> None:
    from deerflow.config.mcp_store import DbMcpServerStore, McpServerConfigConflictError

    store = DbMcpServerStore(database_config=_db_config(tmp_path))
    first = store.save_mcp_servers(
        {
            "github": McpServerConfig.model_validate(
                {
                    "type": "stdio",
                    "command": "npx",
                    "env": {"GITHUB_TOKEN": "v1"},
                }
            )
        }
    )
    store.save_mcp_servers(
        {
            "github": McpServerConfig.model_validate(
                {
                    "type": "stdio",
                    "command": "npx",
                    "env": {"GITHUB_TOKEN": "v2"},
                }
            )
        },
        expected_revision=first.revision,
        expected_content_hash=first.content_hash,
    )

    with pytest.raises(McpServerConfigConflictError):
        store.save_mcp_servers(
            {
                "github": McpServerConfig.model_validate(
                    {
                        "type": "stdio",
                        "command": "npx",
                        "env": {"GITHUB_TOKEN": "stale"},
                    }
                )
            },
            expected_revision=first.revision,
            expected_content_hash=first.content_hash,
        )
