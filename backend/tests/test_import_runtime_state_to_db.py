from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.runtime_config.model import RuntimeConfigRow


def test_collect_runtime_state_inventory_reports_sources_and_risks(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import collect_runtime_state_inventory

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()

    (project_root / "config.yaml").write_text(
        "sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\nmodels: []\n",
        encoding="utf-8",
    )
    (project_root / "extensions_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "type": "stdio",
                        "command": "npx",
                        "env": {"TOKEN": "plain-secret"},
                    },
                    "remote": {
                        "type": "http",
                        "url": "https://mcp.example.test",
                        "headers": {"Authorization": "$MCP_AUTH_HEADER"},
                    },
                },
                "skills": {"writer": {"enabled": False}},
            }
        ),
        encoding="utf-8",
    )

    (state_dir / "USER.md").write_text("profile", encoding="utf-8")
    (state_dir / "SOUL.md").write_text("default soul", encoding="utf-8")
    (state_dir / "memory.json").write_text('{"facts": []}', encoding="utf-8")
    legacy_agent = state_dir / "agents" / "legacy-agent"
    legacy_agent.mkdir(parents=True)
    (legacy_agent / "config.yaml").write_text("name: legacy-agent\ndescription: legacy\n", encoding="utf-8")
    (legacy_agent / "SOUL.md").write_text("legacy soul", encoding="utf-8")
    (legacy_agent / "memory.json").write_text('{"legacy": true}', encoding="utf-8")

    user_agent = state_dir / "users" / "alice" / "agents" / "helper"
    user_agent.mkdir(parents=True)
    (user_agent / "config.yaml").write_text("name: helper\ndescription: helper\n", encoding="utf-8")
    (user_agent / "SOUL.md").write_text("helper soul", encoding="utf-8")
    (user_agent / "memory.json").write_text('{"agent": true}', encoding="utf-8")
    (state_dir / "users" / "alice" / "memory.json").write_text('{"user": true}', encoding="utf-8")

    for category, name in (("public", "browser"), ("custom", "research")):
        skill_dir = skills_root / category / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n", encoding="utf-8")
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "notes.md").write_text("notes", encoding="utf-8")

    report = collect_runtime_state_inventory(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
    )

    assert report["config"]["exists"] is True
    assert report["extensions"]["path"].endswith("extensions_config.json")
    assert {server["name"] for server in report["extensions"]["mcp_servers"]} == {"local", "remote"}
    assert report["extensions"]["mcp_compatibility"] == [
        {
            "name": "local",
            "enabled": True,
            "transport": "stdio",
            "strict_stateless": False,
            "runtime_mode": None,
        },
        {
            "name": "remote",
            "enabled": True,
            "transport": "http",
            "strict_stateless": True,
            "runtime_mode": "remote",
        },
    ]
    assert {"server": "local", "risk": "stdio-mcp-stateful"} in report["extensions"]["risks"]
    assert {"server": "local", "risk": "resolved-secret", "field": "env.TOKEN"} in report["extensions"]["risks"]
    assert {
        "server": "remote",
        "risk": "env-ref",
        "field": "headers.Authorization",
        "env": "MCP_AUTH_HEADER",
    } in report["extensions"]["risks"]
    assert report["extensions"]["skill_states"] == [{"name": "writer", "enabled": False}]
    assert {agent["name"] for agent in report["agents"]} == {"helper", "legacy-agent"}
    assert report["default_agent_souls"] == [
        {
            "owner_user_id": "default",
            "path": str(state_dir / "SOUL.md"),
            "legacy": True,
        }
    ]
    assert {memory["agent_scope"] for memory in report["memory"]} == {"", "helper", "legacy-agent"}
    assert {skill["name"] for skill in report["skills"]} == {"browser", "research"}
    assert report["summary"]["config_files"] == 2
    assert report["summary"]["mcp_servers"] == 2
    assert report["summary"]["agents"] == 2
    assert report["summary"]["default_agent_souls"] == 1
    assert report["summary"]["memory_files"] == 4
    assert report["summary"]["skills"] == 2
    assert report["summary"]["user_profiles"] == 1


def test_collect_runtime_state_inventory_reports_channel_runtime_config(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import collect_runtime_state_inventory

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skills_root.mkdir()
    runtime_config_path = state_dir / "channels" / "runtime-config.json"
    runtime_config_path.parent.mkdir()
    runtime_config_path.write_text(
        json.dumps(
            {
                "slack": {"enabled": True, "bot_token": "xoxb-runtime"},
                "telegram": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )

    report = collect_runtime_state_inventory(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
    )

    assert report["channel_runtime"] == {
        "exists": True,
        "path": str(runtime_config_path),
        "providers": [
            {"name": "slack", "enabled": True, "field_count": 2},
            {"name": "telegram", "enabled": False, "field_count": 1},
        ],
        "risks": [{"risk": "resolved-secret", "provider": "slack", "field": "slack.bot_token"}],
        "error": None,
    }
    assert report["summary"]["channel_runtime_configs"] == 1
    assert report["summary"]["risks"] == 1


def test_import_runtime_state_preflight_reports_invalid_channel_runtime_config(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skills_root.mkdir()
    runtime_config_path = state_dir / "channels" / "runtime-config.json"
    runtime_config_path.parent.mkdir()
    runtime_config_path.write_text("{bad json", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert report["preflight"]["ok"] is False
    assert len(report["preflight"]["errors"]) == 1
    error = report["preflight"]["errors"][0]
    assert error["resource"] == "channel_runtime_config"
    assert error["code"] == "invalid_channel_runtime_config"
    assert error["path"] == str(runtime_config_path)
    assert error["error"].startswith("invalid json:")
    assert Path(database.sqlite_path).exists() is False


def test_collect_runtime_state_inventory_reports_app_config_secret_risks(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import collect_runtime_state_inventory

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skills_root.mkdir()
    (project_root / "config.yaml").write_text(
        """
models:
  - name: referenced
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
  - name: literal
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: literal-secret
""",
        encoding="utf-8",
    )

    report = collect_runtime_state_inventory(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
    )

    assert {
        "risk": "env-ref",
        "field": "models[0].api_key",
        "env": "OPENAI_API_KEY",
    } in report["config"]["risks"]
    assert {
        "risk": "resolved-secret",
        "field": "models[1].api_key",
    } in report["config"]["risks"]
    assert report["summary"]["risks"] == 2


def test_import_runtime_state_dry_run_does_not_create_database(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (project_root / "config.yaml").write_text(
        "sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\n",
        encoding="utf-8",
    )

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert Path(database.sqlite_path).exists() is False


def test_import_runtime_state_preflight_reports_invalid_memory_without_creating_database(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "memory.json").write_text("[]", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert report["preflight"]["ok"] is False
    assert report["preflight"]["errors"] == [
        {
            "resource": "memory",
            "code": "invalid_memory",
            "path": str(state_dir / "memory.json"),
            "error": "top-level JSON value is not an object",
        }
    ]
    assert Path(database.sqlite_path).exists() is False


def test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database(
    tmp_path,
) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (project_root / "config.yaml").write_text(
        "sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\nmodels: not-a-list\n",
        encoding="utf-8",
    )

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert report["preflight"]["ok"] is False
    assert len(report["preflight"]["errors"]) == 1
    error = report["preflight"]["errors"][0]
    assert error["resource"] == "app_config"
    assert error["code"] == "invalid_app_config"
    assert error["path"] == str(project_root / "config.yaml")
    assert "models" in error["error"]
    assert Path(database.sqlite_path).exists() is False


def test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database(
    tmp_path,
) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (project_root / "extensions_config.json").write_text(
        json.dumps({"mcpServers": [], "skills": {}}),
        encoding="utf-8",
    )

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert report["preflight"]["ok"] is False
    assert len(report["preflight"]["errors"]) == 1
    error = report["preflight"]["errors"][0]
    assert error["resource"] == "extensions_config"
    assert error["code"] == "invalid_extensions_config"
    assert error["path"] == str(project_root / "extensions_config.json")
    assert "mcpServers" in error["error"]
    assert Path(database.sqlite_path).exists() is False


def test_import_runtime_state_preflight_blocks_oversized_skill_support_files(tmp_path, monkeypatch) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skill_dir = skills_root / "custom" / "research"
    support_file = skill_dir / "assets" / "large.bin"
    support_file.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: research\ndescription: Research skill\n---\n", encoding="utf-8")
    support_file.write_bytes(b"12345")
    monkeypatch.setenv("DEER_FLOW_DB_SKILL_FILE_MAX_BYTES", "4")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert report["summary"]["risks"] == 1
    assert report["skills"][0]["support_file_bytes"] == 5
    assert report["skills"][0]["risks"] == [
        {
            "risk": "skill-file-too-large",
            "path": str(support_file),
            "bytes": 5,
            "max_bytes": 4,
        }
    ]
    assert report["preflight"]["ok"] is False
    assert report["preflight"]["errors"] == [
        {
            "resource": "skill_file",
            "code": "skill_file_too_large",
            "category": "custom",
            "name": "research",
            "path": str(support_file),
            "error": "skill support file exceeds DEER_FLOW_DB_SKILL_FILE_MAX_BYTES (5 > 4)",
        }
    ]
    assert Path(database.sqlite_path).exists() is False


def test_import_runtime_state_preflight_blocks_oversized_skill_package(tmp_path, monkeypatch) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skill_dir = skills_root / "custom" / "research"
    assets_dir = skill_dir / "assets"
    assets_dir.mkdir(parents=True)
    skill_md_content = "---\nname: research\ndescription: Research skill\n---\n"
    (skill_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
    first_file = assets_dir / "a.bin"
    second_file = assets_dir / "b.bin"
    first_file.write_bytes(b"123")
    second_file.write_bytes(b"456")
    skill_md_bytes = len(skill_md_content.encode("utf-8"))
    package_bytes = skill_md_bytes + 6
    monkeypatch.setenv("DEER_FLOW_DB_SKILL_FILE_MAX_BYTES", "4")
    monkeypatch.setenv("DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES", str(package_bytes - 1))

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert report["summary"]["risks"] == 1
    assert report["skills"][0]["support_file_bytes"] == 6
    assert report["skills"][0]["package_bytes"] == package_bytes
    assert report["skills"][0]["risks"] == [
        {
            "risk": "skill-package-too-large",
            "path": str(skill_dir),
            "bytes": package_bytes,
            "max_bytes": package_bytes - 1,
            "skill_md_bytes": skill_md_bytes,
            "support_file_bytes": 6,
        }
    ]
    assert report["preflight"]["ok"] is False
    assert report["preflight"]["errors"] == [
        {
            "resource": "skill_package",
            "code": "skill_package_too_large",
            "category": "custom",
            "name": "research",
            "path": str(skill_dir),
            "error": f"skill package exceeds DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES ({package_bytes} > {package_bytes - 1})",
        }
    ]
    assert Path(database.sqlite_path).exists() is False


def test_import_runtime_state_apply_refuses_existing_db_conflicts(tmp_path) -> None:
    from deerflow.config.agent_store import DbAgentStore
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "USER.md").write_text("new profile", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    store = DbAgentStore(database_config=database)
    store.save_user_profile("default", "existing profile")

    with pytest.raises(RuntimeError, match="Preflight failed"):
        import_runtime_state_to_db(
            project_root=project_root,
            state_dir=state_dir,
            skills_root=skills_root,
            database_config=database,
            apply=True,
        )

    assert store.load_user_profile("default") == "existing profile"


def test_import_runtime_state_apply_overwrite_replaces_existing_db_conflicts(tmp_path) -> None:
    from deerflow.config.agent_store import DbAgentStore
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "USER.md").write_text("replacement profile", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    store = DbAgentStore(database_config=database)
    store.save_user_profile("default", "existing profile")

    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        overwrite=True,
    )

    assert report["preflight"]["ok"] is True
    assert report["preflight"]["overwrite_enabled"] is True
    assert report["preflight"]["conflicts"] == [{"resource": "user_profile", "owner_user_id": "default"}]
    assert report["applied"]["overwritten_conflicts"] == 1
    assert store.load_user_profile("default") == "replacement profile"


def test_import_runtime_state_apply_overwrite_replaces_default_agent_soul(tmp_path) -> None:
    from deerflow.config.agent_store import DbAgentStore
    from deerflow.persistence.agents.model import DefaultAgentSoulRow
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "SOUL.md").write_text("replacement default soul", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    store = DbAgentStore(database_config=database)
    store.save_default_agent_soul("default", "existing default soul")

    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        overwrite=True,
    )

    assert report["preflight"]["conflicts"] == [{"resource": "default_agent_soul", "owner_user_id": "default"}]
    assert report["applied"]["overwritten_conflicts"] == 1
    assert report["applied"]["overwritten_by_resource"] == {"default_agent_soul": 1}
    assert store.load_default_agent_soul("default") == "replacement default soul"

    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with Session(engine) as session:
        row = session.get(DefaultAgentSoulRow, "default")
        assert row is not None
        assert row.revision == 2


def test_import_runtime_state_apply_overwrite_reports_conflicts_by_resource(tmp_path) -> None:
    from deerflow.agents.memory.storage import DbMemoryStorage
    from deerflow.config.agent_store import DbAgentStore
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "USER.md").write_text("replacement profile", encoding="utf-8")
    (state_dir / "memory.json").write_text('{"version": "1.0", "facts": [{"text": "replacement"}]}', encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    agent_store = DbAgentStore(database_config=database)
    agent_store.save_user_profile("default", "existing profile")
    memory_storage = DbMemoryStorage(database_config=database)
    assert memory_storage.save({"version": "1.0", "facts": [{"text": "existing"}]}, user_id="default")

    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        overwrite=True,
    )

    assert report["applied"]["overwritten_conflicts"] == 2
    assert report["applied"]["overwritten_by_resource"] == {
        "memory": 1,
        "user_profile": 1,
    }
    assert agent_store.load_user_profile("default") == "replacement profile"
    assert memory_storage.reload(user_id="default")["facts"][0]["text"] == "replacement"


def test_import_runtime_state_apply_overwrite_replaces_agents_and_custom_skills(tmp_path) -> None:
    from deerflow.config.agent_store import DbAgentStore
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()

    agent_dir = state_dir / "agents" / "research-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text("name: research-agent\ndescription: Replacement agent\n", encoding="utf-8")
    (agent_dir / "SOUL.md").write_text("replacement soul", encoding="utf-8")

    skill_dir = skills_root / "custom" / "research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: research\ndescription: Replacement skill\n---\n", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    agent_store = DbAgentStore(database_config=database)
    agent_store.save_agent(
        "default",
        "research-agent",
        {"name": "research-agent", "description": "Existing agent"},
        "existing soul",
    )
    skill_storage = DbSkillStorage(host_path=str(tmp_path / "existing-skills"), database_config=database)
    skill_storage.write_custom_skill("research", "SKILL.md", "---\nname: research\ndescription: Existing skill\n---\n")

    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        overwrite=True,
    )

    assert report["applied"]["overwritten_by_resource"] == {
        "agent": 1,
        "skill": 1,
    }
    assert agent_store.load_agent_config("default", "research-agent").description == "Replacement agent"
    assert agent_store.load_agent_soul("default", "research-agent") == "replacement soul"
    assert "Replacement skill" in skill_storage.read_custom_skill("research")


def test_import_runtime_state_preflight_validates_agent_config_semantics(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()

    agent_dir = state_dir / "agents" / "bad-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text("name: bad-agent\ndescription:\n  - not\n  - string\n", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["mode"] == "dry-run"
    assert report["preflight"]["ok"] is False
    assert len(report["preflight"]["errors"]) == 1
    error = report["preflight"]["errors"][0]
    assert error["resource"] == "agent"
    assert error["code"] == "invalid_agent_config"
    assert error["name"] == "bad-agent"
    assert "description" in error["error"]
    assert Path(database.sqlite_path).exists() is False


def test_import_runtime_state_apply_writes_config_extensions_and_mcp(tmp_path) -> None:
    from deerflow.config.mcp_store import DbMcpServerStore
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (project_root / "config.yaml").write_text(
        "sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\nmodels: []\n",
        encoding="utf-8",
    )
    (project_root / "extensions_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "stdio",
                        "command": "npx",
                        "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
                    }
                },
                "skills": {"writer": {"enabled": False}},
            }
        ),
        encoding="utf-8",
    )

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        updated_by="migration-test",
    )

    assert report["mode"] == "apply"
    assert report["applied"] == {
        "app_config": True,
        "channel_runtime_config": False,
        "extensions_config": True,
        "mcp_servers": 1,
        "user_profiles": 0,
        "default_agent_souls": 0,
        "agents": 0,
        "memory_files": 0,
        "skills": 0,
        "public_skills": 0,
        "custom_skills": 0,
        "skipped_public_skills": 0,
    }

    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with Session(engine) as session:
        app_row = session.get(RuntimeConfigRow, "app")
        assert app_row is not None
        assert app_row.payload_json["sandbox"]["use"] == "deerflow.sandbox.local:LocalSandboxProvider"
        assert app_row.updated_by == "migration-test"

        extensions_row = session.get(RuntimeConfigRow, "extensions")
        assert extensions_row is not None
        assert extensions_row.payload_json == {"skills": {"writer": {"enabled": False}}}

    mcp_loaded = DbMcpServerStore(database_config=database).load_extensions_config()
    assert mcp_loaded.config.mcp_servers["github"].env == {"GITHUB_TOKEN": "$GITHUB_TOKEN"}


def test_import_runtime_state_apply_writes_channel_runtime_config(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    (state_dir / "channels").mkdir(parents=True)
    (state_dir / "channels" / "runtime-config.json").write_text(
        json.dumps(
            {
                "slack": {
                    "enabled": True,
                    "bot_token": "xoxb-ui",
                    "app_token": "xapp-ui",
                }
            }
        ),
        encoding="utf-8",
    )

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        updated_by="migration-test",
    )

    assert report["applied"]["channel_runtime_config"] is True
    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with Session(engine) as session:
        row = session.get(RuntimeConfigRow, "channel_runtime")
        assert row is not None
        assert row.payload_json == {
            "slack": {
                "enabled": True,
                "bot_token": "xoxb-ui",
                "app_token": "xapp-ui",
            }
        }
        assert row.updated_by == "migration-test"


def test_import_runtime_state_preflight_reports_channel_runtime_conflict(tmp_path) -> None:
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    (state_dir / "channels").mkdir(parents=True)
    (state_dir / "channels" / "runtime-config.json").write_text(
        json.dumps({"slack": {"enabled": True, "bot_token": "replacement"}}),
        encoding="utf-8",
    )

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    Path(database.sqlite_path).parent.mkdir(parents=True)
    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    RuntimeConfigRow.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            RuntimeConfigRow(
                key="channel_runtime",
                payload_json={"slack": {"enabled": True, "bot_token": "existing"}},
                schema_version="1",
                revision=1,
                content_hash="seed",
            )
        )
        session.commit()

    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=False,
    )

    assert report["preflight"]["ok"] is False
    assert report["preflight"]["conflicts"] == [
        {"resource": "channel_runtime_config", "key": "channel_runtime"}
    ]


def test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp(tmp_path) -> None:
    from deerflow.config.mcp_store import DbMcpServerStore
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))

    (project_root / "config.yaml").write_text(
        "sandbox:\n  use: old.Provider\nmodels: []\nlog_level: warning\n",
        encoding="utf-8",
    )
    (project_root / "extensions_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "stdio",
                        "command": "old-npx",
                        "env": {"GITHUB_TOKEN": "$OLD_TOKEN"},
                    }
                },
                "skills": {"writer": {"enabled": False}},
            }
        ),
        encoding="utf-8",
    )
    import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        updated_by="seed",
    )

    (project_root / "config.yaml").write_text(
        "sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\nmodels: []\nlog_level: debug\n",
        encoding="utf-8",
    )
    (project_root / "extensions_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "stdio",
                        "command": "new-npx",
                        "env": {"GITHUB_TOKEN": "$NEW_TOKEN"},
                    }
                },
                "skills": {"writer": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        overwrite=True,
        updated_by="overwrite",
    )

    assert report["applied"]["overwritten_conflicts"] == 3
    assert report["applied"]["overwritten_by_resource"] == {
        "app_config": 1,
        "extensions_config": 1,
        "mcp_server": 1,
    }
    restart_required = report["applied"]["restart_required"]["app_config"]
    assert restart_required["fields"] == ["log_level", "sandbox"]
    assert "apply_logging_level()" in restart_required["reasons"]["log_level"]
    assert "sandbox" in restart_required["reasons"]["sandbox"].lower()

    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with Session(engine) as session:
        app_row = session.get(RuntimeConfigRow, "app")
        assert app_row is not None
        assert app_row.payload_json["log_level"] == "debug"
        assert app_row.updated_by == "overwrite"

        extensions_row = session.get(RuntimeConfigRow, "extensions")
        assert extensions_row is not None
        assert extensions_row.payload_json == {"skills": {"writer": {"enabled": True}}}
        assert extensions_row.updated_by == "overwrite"

    mcp_loaded = DbMcpServerStore(database_config=database).load_extensions_config()
    github = mcp_loaded.config.mcp_servers["github"]
    assert github.command == "new-npx"
    assert github.env == {"GITHUB_TOKEN": "$NEW_TOKEN"}


def test_import_runtime_state_cli_apply_overwrite_uses_bootstrap_database_url(tmp_path) -> None:
    import os
    import subprocess
    import sys

    from deerflow.config.agent_store import DbAgentStore

    backend_root = Path(__file__).resolve().parents[1]
    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skills_root.mkdir(parents=True)
    (state_dir / "USER.md").write_text("replacement profile", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    store = DbAgentStore(database_config=database)
    store.save_user_profile("default", "existing profile")

    env = os.environ.copy()
    env["DEER_FLOW_DATABASE_URL"] = f"sqlite:///{database.sqlite_path}"
    pythonpath_entries = [str(backend_root), str(backend_root / "packages" / "harness")]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/import_runtime_state_to_db.py",
            "--project-root",
            str(project_root),
            "--state-dir",
            str(state_dir),
            "--skills-root",
            str(skills_root),
            "--apply",
            "--overwrite",
            "--updated-by",
            "cli-test",
        ],
        cwd=backend_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["database"]["backend"] == "sqlite"
    assert report["database"]["sqlite_path"] == database.sqlite_path
    assert report["applied"]["overwritten_conflicts"] == 1
    assert report["applied"]["overwritten_by_resource"] == {"user_profile": 1}
    assert store.load_user_profile("default") == "replacement profile"


def test_import_runtime_state_cli_apply_overwrite_accepts_database_url_argument(tmp_path) -> None:
    import os
    import subprocess
    import sys

    from deerflow.config.agent_store import DbAgentStore

    backend_root = Path(__file__).resolve().parents[1]
    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skills_root.mkdir(parents=True)
    (state_dir / "USER.md").write_text("replacement profile", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    store = DbAgentStore(database_config=database)
    store.save_user_profile("default", "existing profile")

    env = os.environ.copy()
    env.pop("DEER_FLOW_DATABASE_URL", None)
    pythonpath_entries = [str(backend_root), str(backend_root / "packages" / "harness")]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/import_runtime_state_to_db.py",
            "--project-root",
            str(project_root),
            "--state-dir",
            str(state_dir),
            "--skills-root",
            str(skills_root),
            "--database-url",
            f"sqlite:///{database.sqlite_path}",
            "--apply",
            "--overwrite",
            "--updated-by",
            "cli-arg-test",
        ],
        cwd=backend_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["database"]["backend"] == "sqlite"
    assert report["database"]["sqlite_path"] == database.sqlite_path
    assert report["applied"]["overwritten_by_resource"] == {"user_profile": 1}
    assert store.load_user_profile("default") == "replacement profile"


def test_import_runtime_state_cli_reports_invalid_database_url_without_traceback(tmp_path) -> None:
    import os
    import subprocess
    import sys

    backend_root = Path(__file__).resolve().parents[1]
    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skills_root.mkdir(parents=True)

    env = os.environ.copy()
    env.pop("DEER_FLOW_DATABASE_URL", None)
    pythonpath_entries = [str(backend_root), str(backend_root / "packages" / "harness")]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/import_runtime_state_to_db.py",
            "--project-root",
            str(project_root),
            "--state-dir",
            str(state_dir),
            "--skills-root",
            str(skills_root),
            "--database-url",
            "not-a-db-url",
            "--apply",
        ],
        cwd=backend_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must be a sqlite or postgresql SQLAlchemy URL" in result.stderr
    assert "Traceback" not in result.stderr


def test_import_runtime_state_cli_help_does_not_load_runtime_config_warnings() -> None:
    import os
    import subprocess
    import sys

    backend_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    pythonpath_entries = [str(backend_root), str(backend_root / "packages" / "harness")]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    result = subprocess.run(
        [sys.executable, "scripts/import_runtime_state_to_db.py", "--help"],
        cwd=backend_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Plan import of Harness runtime state" in result.stdout
    assert result.stderr == ""


def test_import_runtime_state_apply_writes_agents_and_user_profiles(tmp_path) -> None:
    from deerflow.config.agent_store import DbAgentStore
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "USER.md").write_text("default profile", encoding="utf-8")
    (state_dir / "SOUL.md").write_text("default soul", encoding="utf-8")

    legacy_agent = state_dir / "agents" / "legacy-agent"
    legacy_agent.mkdir(parents=True)
    (legacy_agent / "config.yaml").write_text(
        "name: legacy-agent\ndescription: Legacy agent\n",
        encoding="utf-8",
    )
    (legacy_agent / "SOUL.md").write_text("legacy soul", encoding="utf-8")

    user_agent = state_dir / "users" / "alice" / "agents" / "helper"
    user_agent.mkdir(parents=True)
    (user_agent / "config.yaml").write_text(
        "name: helper\ndescription: Helper agent\n",
        encoding="utf-8",
    )
    (user_agent / "SOUL.md").write_text("helper soul", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        updated_by="migration-test",
    )

    assert report["applied"]["user_profiles"] == 1
    assert report["applied"]["default_agent_souls"] == 1
    assert report["applied"]["agents"] == 2

    store = DbAgentStore(database_config=database)
    assert store.load_user_profile("default") == "default profile"
    assert store.load_default_agent_soul("default") == "default soul"
    assert store.load_agent_config("default", "legacy-agent").description == "Legacy agent"
    assert store.load_agent_soul("default", "legacy-agent") == "legacy soul"
    assert store.load_agent_config("alice", "helper").description == "Helper agent"
    assert store.load_agent_soul("alice", "helper") == "helper soul"


def test_import_runtime_state_apply_writes_default_agent_soul(tmp_path) -> None:
    from deerflow.config.agent_store import DbAgentStore
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "SOUL.md").write_text("default soul", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        updated_by="migration-test",
    )

    assert report["applied"]["default_agent_souls"] == 1
    store = DbAgentStore(database_config=database)
    assert store.load_default_agent_soul("default") == "default soul"


def test_import_runtime_state_apply_writes_memory_scopes(tmp_path) -> None:
    from deerflow.agents.memory.storage import DbMemoryStorage
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    (state_dir / "memory.json").write_text('{"version": "1.0", "facts": [{"text": "default"}]}', encoding="utf-8")

    legacy_agent = state_dir / "agents" / "legacy-agent"
    legacy_agent.mkdir(parents=True)
    (legacy_agent / "config.yaml").write_text("name: legacy-agent\n", encoding="utf-8")
    (legacy_agent / "memory.json").write_text('{"version": "1.0", "facts": [{"text": "legacy-agent"}]}', encoding="utf-8")

    user_root = state_dir / "users" / "alice"
    user_root.mkdir(parents=True)
    (user_root / "memory.json").write_text('{"version": "1.0", "facts": [{"text": "alice"}]}', encoding="utf-8")
    user_agent = user_root / "agents" / "helper"
    user_agent.mkdir(parents=True)
    (user_agent / "config.yaml").write_text("name: helper\n", encoding="utf-8")
    (user_agent / "memory.json").write_text('{"version": "1.0", "facts": [{"text": "helper"}]}', encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        updated_by="migration-test",
    )

    assert report["applied"]["memory_files"] == 4

    storage = DbMemoryStorage(database_config=database)
    assert storage.load(user_id="default")["facts"][0]["text"] == "default"
    assert storage.load("legacy-agent", user_id="default")["facts"][0]["text"] == "legacy-agent"
    assert storage.load(user_id="alice")["facts"][0]["text"] == "alice"
    assert storage.load("helper", user_id="alice")["facts"][0]["text"] == "helper"


def test_import_runtime_state_apply_writes_public_and_custom_skills(tmp_path) -> None:
    import shutil

    from deerflow.skills.storage.db_skill_storage import DbSkillStorage
    from deerflow.skills.types import SkillCategory
    from scripts.import_runtime_state_to_db import import_runtime_state_to_db

    project_root = tmp_path / "project"
    state_dir = tmp_path / "state"
    skills_root = project_root / "skills"
    project_root.mkdir()
    state_dir.mkdir()
    skill_dir = skills_root / "custom" / "research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research skill\n---\n\nUse sources.\n",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "notes.md").write_text("support notes", encoding="utf-8")

    public_skill = skills_root / "public" / "browser"
    public_skill.mkdir(parents=True)
    (public_skill / "SKILL.md").write_text(
        "---\nname: browser\ndescription: Browser skill\n---\n",
        encoding="utf-8",
    )
    (public_skill / "references").mkdir()
    (public_skill / "references" / "usage.md").write_text("browser support notes", encoding="utf-8")

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    report = import_runtime_state_to_db(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=skills_root,
        database_config=database,
        apply=True,
        updated_by="migration-test",
    )

    assert report["applied"]["skills"] == 2
    assert report["applied"]["public_skills"] == 1
    assert report["applied"]["custom_skills"] == 1
    skill_actions = {(skill["category"], skill["name"]): skill["import_action"] for skill in report["skills"]}
    assert skill_actions == {
        ("custom", "research"): "import-to-db",
        ("public", "browser"): "import-to-db",
    }
    custom_report = next(skill for skill in report["skills"] if skill["category"] == "custom")
    public_report = next(skill for skill in report["skills"] if skill["category"] == "public")
    assert custom_report["storage_boundary"] == "db"
    assert custom_report["runtime_artifact_required"] is False
    assert public_report["storage_boundary"] == "db"
    assert public_report["runtime_artifact_required"] is False
    assert public_report["deployment_artifact"] is None

    materialized_root = tmp_path / "materialized-skills"
    storage = DbSkillStorage(host_path=str(materialized_root), database_config=database)
    shutil.rmtree(skills_root)
    shutil.rmtree(materialized_root / "custom", ignore_errors=True)
    shutil.rmtree(materialized_root / "public", ignore_errors=True)
    rematerialized = storage.get_custom_skill_dir("research")

    assert (rematerialized / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname: research")
    assert (rematerialized / "references" / "notes.md").read_text(encoding="utf-8") == "support notes"
    assert storage.public_skill_exists("browser")
    assert storage.read_skill_file("browser", SkillCategory.PUBLIC, "references/usage.md") == "browser support notes"
    loaded_public = next(skill for skill in storage.load_skills(enabled_only=False) if skill.name == "browser")
    assert loaded_public.category == SkillCategory.PUBLIC
