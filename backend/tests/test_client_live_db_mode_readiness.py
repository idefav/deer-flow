from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from deerflow.config.app_config import get_app_config, reset_app_config
from deerflow.persistence.base import Base
from deerflow.persistence.engine import close_engine
from deerflow.persistence.runtime_config.model import RuntimeConfigRow
from deerflow.persistence.runtime_config.sql import stable_json_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_LIVE_TEST_PATH = REPO_ROOT / "backend" / "tests" / "test_client_live.py"


def _seed_db_app_config(db_path: Path) -> None:
    payload = {
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        "models": [
            {
                "name": "db-live",
                "use": "langchain_openai:ChatOpenAI",
                "model": "gpt-db",
            }
        ],
        "database": {"backend": "memory"},
    }
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                RuntimeConfigRow(
                    key="app",
                    payload_json=payload,
                    schema_version="1",
                    revision=1,
                    content_hash=stable_json_hash(payload),
                    updated_by="test",
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _import_client_live_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, CLIENT_LIVE_TEST_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_client_live_entrypoint_uses_db_config_readiness(tmp_path, monkeypatch, caplog) -> None:
    db_path = tmp_path / "deerflow.db"
    _seed_db_app_config(db_path)

    reset_app_config()
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    caplog.set_level("ERROR")
    module_name = "test_client_live_db_mode_ready"
    sys.modules.pop(module_name, None)
    try:
        module = _import_client_live_module(module_name)

        skip_markers = [marker for marker in module.pytestmark if getattr(marker, "name", "") == "skip"]
        assert skip_markers == []
        assert get_app_config().models[0].name == "db-live"
        assert "DB config mode requires load_and_cache_db_app_config" not in caplog.text
    finally:
        sys.modules.pop(module_name, None)
        reset_app_config()
        asyncio.run(close_engine())


def test_client_live_entrypoint_uses_shared_requires_llm_skip_reason(monkeypatch) -> None:
    import support.live_gate_readiness as live_gate_readiness

    reset_app_config()
    monkeypatch.setattr(
        live_gate_readiness,
        "requires_llm_skip_reason",
        lambda: "shared requires_llm readiness blocked",
    )
    monkeypatch.setattr(
        live_gate_readiness,
        "load_active_app_config_for_requires_llm",
        lambda: (_ for _ in ()).throw(AssertionError("loader should not run when readiness blocks")),
    )
    module_name = "test_client_live_shared_skip_reason"
    sys.modules.pop(module_name, None)
    try:
        module = _import_client_live_module(module_name)

        skip_markers = [marker for marker in module.pytestmark if getattr(marker, "name", "") == "skip"]
        assert [marker.kwargs.get("reason") for marker in skip_markers] == [
            "shared requires_llm readiness blocked"
        ]
    finally:
        sys.modules.pop(module_name, None)
        reset_app_config()


def test_client_live_entrypoint_loads_active_config_after_shared_readiness(monkeypatch) -> None:
    import support.live_gate_readiness as live_gate_readiness

    reset_app_config()
    calls = []
    monkeypatch.setattr(live_gate_readiness, "requires_llm_skip_reason", lambda: None)
    monkeypatch.setattr(
        live_gate_readiness,
        "load_active_app_config_for_requires_llm",
        lambda: calls.append("load-active-config"),
    )
    module_name = "test_client_live_shared_ready_loader"
    sys.modules.pop(module_name, None)
    try:
        module = _import_client_live_module(module_name)

        skip_markers = [marker for marker in module.pytestmark if getattr(marker, "name", "") == "skip"]
        assert skip_markers == []
        assert calls == ["load-active-config"]
    finally:
        sys.modules.pop(module_name, None)
        reset_app_config()
