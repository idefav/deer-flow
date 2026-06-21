from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from deerflow.agents.memory.storage import DbMemoryStorage, create_empty_memory, get_memory_storage
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.memory_config import MemoryConfig
from deerflow.persistence.memory.model import MemoryRow


def _db_config(tmp_path: Path) -> DatabaseConfig:
    return DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path))


def _nested_db_config(tmp_path: Path) -> DatabaseConfig:
    return DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))


def test_db_memory_storage_missing_row_returns_empty_memory(tmp_path) -> None:
    storage = DbMemoryStorage(database_config=_db_config(tmp_path))

    memory = storage.load(user_id="alice")

    assert memory["version"] == "1.0"
    assert memory["facts"] == []


def test_db_memory_storage_creates_sqlite_parent_dir(tmp_path) -> None:
    database = _nested_db_config(tmp_path)
    storage = DbMemoryStorage(database_config=database)

    assert storage.save(create_empty_memory(), user_id="alice") is True
    assert Path(database.sqlite_path).exists()


def test_db_memory_storage_save_and_load_user_scope(tmp_path) -> None:
    storage = DbMemoryStorage(database_config=_db_config(tmp_path))
    memory = create_empty_memory()
    memory["user"]["workContext"]["summary"] = "Alice context"

    assert storage.save(memory, user_id="alice") is True

    loaded = storage.load(user_id="alice")
    assert loaded["user"]["workContext"]["summary"] == "Alice context"
    assert loaded["lastUpdated"]


def test_db_memory_storage_user_and_agent_scopes_are_isolated(tmp_path) -> None:
    storage = DbMemoryStorage(database_config=_db_config(tmp_path))
    global_memory = create_empty_memory()
    global_memory["user"]["workContext"]["summary"] = "global"
    agent_memory = create_empty_memory()
    agent_memory["user"]["workContext"]["summary"] = "agent"

    storage.save(global_memory, user_id="alice")
    storage.save(agent_memory, "research-agent", user_id="alice")

    assert storage.load(user_id="alice")["user"]["workContext"]["summary"] == "global"
    assert storage.load("research-agent", user_id="alice")["user"]["workContext"]["summary"] == "agent"


def test_db_memory_storage_save_does_not_mutate_caller_dict(tmp_path) -> None:
    storage = DbMemoryStorage(database_config=_db_config(tmp_path))
    original = {"version": "1.0", "facts": []}

    storage.save(original, user_id="alice")

    assert original == {"version": "1.0", "facts": []}
    assert "lastUpdated" in storage.load(user_id="alice")


def test_db_memory_storage_load_refreshes_when_revision_changes(tmp_path) -> None:
    first = DbMemoryStorage(database_config=_db_config(tmp_path))
    second = DbMemoryStorage(database_config=_db_config(tmp_path))
    memory = create_empty_memory()
    memory["user"]["workContext"]["summary"] = "initial"
    first.save(memory, user_id="alice")
    assert first.load(user_id="alice")["user"]["workContext"]["summary"] == "initial"

    updated = create_empty_memory()
    updated["user"]["workContext"]["summary"] = "updated"
    second.save(updated, user_id="alice")

    assert first.load(user_id="alice")["user"]["workContext"]["summary"] == "updated"
    assert first.reload(user_id="alice")["user"]["workContext"]["summary"] == "updated"


def test_db_memory_storage_save_collapses_duplicate_scope_without_db_unique_constraint(tmp_path) -> None:
    database = _db_config(tmp_path)
    first = DbMemoryStorage(database_config=database)
    memory = create_empty_memory()
    memory["user"]["workContext"]["summary"] = "initial"
    assert first.save(memory, user_id="alice") is True

    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    duplicate_memory = create_empty_memory()
    duplicate_memory["user"]["workContext"]["summary"] = "duplicate"
    with Session(engine) as session:
        session.add(
            MemoryRow(
                owner_user_id="alice",
                agent_scope="",
                memory_json=duplicate_memory,
                schema_version="1.0",
                revision=0,
            )
        )
        session.commit()

    replacement = create_empty_memory()
    replacement["user"]["workContext"]["summary"] = "saved"
    second = DbMemoryStorage(database_config=database)
    assert second.save(replacement, user_id="alice") is True

    with Session(engine) as session:
        rows = list(session.execute(select(MemoryRow).where(MemoryRow.owner_user_id == "alice")).scalars())

    assert len(rows) == 1
    assert rows[0].memory_json["user"]["workContext"]["summary"] == "saved"


def test_db_memory_storage_rejects_stale_cached_save(tmp_path) -> None:
    first = DbMemoryStorage(database_config=_db_config(tmp_path))
    second = DbMemoryStorage(database_config=_db_config(tmp_path))
    memory = create_empty_memory()
    memory["user"]["workContext"]["summary"] = "initial"
    assert first.save(memory, user_id="alice") is True

    stale = first.load(user_id="alice")
    current = second.load(user_id="alice")
    current["user"]["workContext"]["summary"] = "second update"
    assert second.save(current, user_id="alice") is True

    stale["user"]["workContext"]["summary"] = "stale overwrite"
    assert first.save(stale, user_id="alice") is False

    assert second.reload(user_id="alice")["user"]["workContext"]["summary"] == "second update"
    assert first.reload(user_id="alice")["user"]["workContext"]["summary"] == "second update"


def test_get_memory_storage_can_load_db_storage(tmp_path) -> None:
    import deerflow.agents.memory.storage as storage_mod

    storage_mod._storage_instance = None
    try:
        with (
            patch(
                "deerflow.agents.memory.storage.get_memory_config",
                return_value=MemoryConfig(storage_class="deerflow.agents.memory.storage.DbMemoryStorage"),
            ),
            patch(
                "deerflow.agents.memory.storage.get_app_config",
                return_value=type("Config", (), {"database": _db_config(tmp_path)})(),
            ),
        ):
            storage = get_memory_storage()

        assert isinstance(storage, DbMemoryStorage)
    finally:
        storage_mod._storage_instance = None


def test_get_memory_storage_db_mode_defaults_to_db_storage(tmp_path, monkeypatch) -> None:
    import deerflow.agents.memory.storage as storage_mod

    storage_mod._storage_instance = None
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    try:
        with (
            patch(
                "deerflow.agents.memory.storage.get_memory_config",
                return_value=MemoryConfig(storage_class="deerflow.agents.memory.storage.FileMemoryStorage"),
            ),
            patch(
                "deerflow.agents.memory.storage.get_app_config",
                return_value=type("Config", (), {"database": _db_config(tmp_path)})(),
            ),
        ):
            storage = get_memory_storage()

        assert isinstance(storage, DbMemoryStorage)
    finally:
        storage_mod._storage_instance = None


def test_get_memory_storage_db_mode_does_not_fall_back_to_file_storage(monkeypatch) -> None:
    import deerflow.agents.memory.storage as storage_mod

    storage_mod._storage_instance = None
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    try:
        with (
            patch(
                "deerflow.agents.memory.storage.get_memory_config",
                return_value=MemoryConfig(storage_class="deerflow.agents.memory.storage.FileMemoryStorage"),
            ),
            patch(
                "deerflow.agents.memory.storage.get_app_config",
                side_effect=RuntimeError("DB config not preloaded"),
            ),
        ):
            with pytest.raises(RuntimeError, match="DB config not preloaded"):
                get_memory_storage()

        assert storage_mod._storage_instance is None
    finally:
        storage_mod._storage_instance = None
