from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from deerflow.config.agent_store import DbAgentStore
from deerflow.config.agents_config import AgentConfig, list_custom_agents, load_agent_config, load_agent_soul
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.agents.model import CustomAgentRow


def _db_config(tmp_path: Path) -> DatabaseConfig:
    return DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path))


def test_db_agent_store_save_load_and_soul(tmp_path) -> None:
    store = DbAgentStore(database_config=_db_config(tmp_path))
    config = AgentConfig(name="researcher", description="Research agent", model="gpt-test", skills=None)

    store.save_agent("alice", "researcher", config, "# Soul")

    loaded = store.load_agent_config("alice", "researcher")
    assert loaded == config
    assert store.load_agent_soul("alice", "researcher") == "# Soul"


def test_db_agent_store_preserves_empty_skills_list(tmp_path) -> None:
    store = DbAgentStore(database_config=_db_config(tmp_path))
    config = AgentConfig(name="quiet-agent", skills=[])

    store.save_agent("alice", "quiet-agent", config, "")

    loaded = store.load_agent_config("alice", "quiet-agent")
    assert loaded is not None
    assert loaded.skills == []


def test_db_agent_store_user_isolation_and_sorted_list(tmp_path) -> None:
    store = DbAgentStore(database_config=_db_config(tmp_path))
    store.save_agent("alice", "z-agent", AgentConfig(name="z-agent"), "z")
    store.save_agent("alice", "a-agent", AgentConfig(name="a-agent"), "a")
    store.save_agent("bob", "b-agent", AgentConfig(name="b-agent"), "b")

    assert [agent.name for agent in store.list_agents("alice")] == ["a-agent", "z-agent"]
    assert [agent.name for agent in store.list_agents("bob")] == ["b-agent"]
    assert store.load_agent_config("alice", "b-agent") is None


def test_db_agent_store_update_and_delete(tmp_path) -> None:
    store = DbAgentStore(database_config=_db_config(tmp_path))
    store.save_agent("alice", "writer", AgentConfig(name="writer", description="old"), "old soul")
    store.save_agent("alice", "writer", AgentConfig(name="writer", description="new"), "new soul")

    loaded = store.load_agent_config("alice", "writer")
    assert loaded is not None
    assert loaded.description == "new"
    assert store.load_agent_soul("alice", "writer") == "new soul"

    assert store.delete_agent("alice", "writer") is True
    assert store.delete_agent("alice", "writer") is False
    assert store.load_agent_config("alice", "writer") is None


def test_db_agent_store_save_agent_collapses_duplicate_business_keys_without_db_unique_constraint(tmp_path) -> None:
    database = _db_config(tmp_path)
    sqlite_path = Path(database.sqlite_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{sqlite_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE custom_agents (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                owner_user_id VARCHAR(128) NOT NULL,
                agent_name VARCHAR(128) NOT NULL,
                config_json JSON NOT NULL,
                soul_text TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
    with Session(engine) as session:
        session.add_all(
            [
                CustomAgentRow(
                    owner_user_id="alice",
                    agent_name="writer",
                    config_json=AgentConfig(name="writer", description="older").model_dump(),
                    soul_text="older soul",
                    revision=1,
                ),
                CustomAgentRow(
                    owner_user_id="alice",
                    agent_name="writer",
                    config_json=AgentConfig(name="writer", description="newer").model_dump(),
                    soul_text="newer soul",
                    revision=2,
                ),
            ]
        )
        session.commit()

    store = DbAgentStore(database_config=database)
    store.save_agent("alice", "writer", AgentConfig(name="writer", description="saved"), "saved soul")

    with Session(engine) as session:
        rows = list(session.execute(select(CustomAgentRow).where(CustomAgentRow.owner_user_id == "alice")).scalars())

    assert len(rows) == 1
    assert rows[0].agent_name == "writer"
    assert rows[0].config_json["description"] == "saved"
    assert rows[0].soul_text == "saved soul"


def test_db_agent_store_user_profile(tmp_path) -> None:
    store = DbAgentStore(database_config=_db_config(tmp_path))

    assert store.load_user_profile("alice") is None

    store.save_user_profile("alice", "Alice profile")
    assert store.load_user_profile("alice") == "Alice profile"

    store.save_user_profile("alice", "Updated profile")
    assert store.load_user_profile("alice") == "Updated profile"


def test_db_agent_store_default_agent_soul(tmp_path) -> None:
    store = DbAgentStore(database_config=_db_config(tmp_path))

    assert store.load_default_agent_soul("alice") is None

    store.save_default_agent_soul("alice", "# Alice default soul")
    assert store.load_default_agent_soul("alice") == "# Alice default soul"

    store.save_default_agent_soul("alice", "# Updated default soul")
    assert store.load_default_agent_soul("alice") == "# Updated default soul"


@pytest.mark.parametrize("name", ["../escape", "bad/name", "bad name", "bad_name"])
def test_db_agent_store_rejects_invalid_agent_names(tmp_path, name: str) -> None:
    store = DbAgentStore(database_config=_db_config(tmp_path))

    with pytest.raises(ValueError):
        store.save_agent("alice", name, AgentConfig(name="valid"), "")


def test_agents_config_helpers_read_from_db_store_in_db_mode(tmp_path, monkeypatch) -> None:
    database = _db_config(tmp_path)
    store = DbAgentStore(database_config=database)
    store.save_agent("alice", "db-agent", AgentConfig(name="db-agent", description="from db"), "# DB soul")

    set_app_config(AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "database": database.model_dump()}))
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    try:
        loaded = load_agent_config("db-agent", user_id="alice")

        assert loaded is not None
        assert loaded.description == "from db"
        assert load_agent_soul("db-agent", user_id="alice") == "# DB soul"
        assert [agent.name for agent in list_custom_agents(user_id="alice")] == ["db-agent"]
    finally:
        reset_app_config()


def test_agents_config_helpers_read_default_agent_soul_from_db_in_db_mode(tmp_path, monkeypatch) -> None:
    from deerflow.config.agents_config import load_agent_soul

    database = _db_config(tmp_path)
    store = DbAgentStore(database_config=database)
    store.save_default_agent_soul("alice", "# DB default soul")
    (tmp_path / "SOUL.md").write_text("# file default soul", encoding="utf-8")

    set_app_config(AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "database": database.model_dump()}))
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    try:
        assert load_agent_soul(None, user_id="alice") == "# DB default soul"
    finally:
        reset_app_config()
