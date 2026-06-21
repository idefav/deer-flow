from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "backend" / "tests"


def _write_provider_neutral_config(config_path: Path) -> None:
    config_path.write_text(
        """
models:
  - name: azure-live
    use: langchain_openai:ChatOpenAI
    model: gpt-4o
    api_key: $AZURE_OPENAI_API_KEY
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
""",
        encoding="utf-8",
    )


def _load_test_module(filename: str):
    path = TESTS_ROOT / filename
    module_name = f"_entrypoint_readiness_{path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _active_skip_reasons(test_func) -> list[str]:
    reasons: list[str] = []
    for mark in getattr(test_func, "pytestmark", []):
        if mark.name == "skip":
            reasons.append(str(mark.kwargs.get("reason", "")))
        elif mark.name == "skipif" and mark.args and bool(mark.args[0]):
            reasons.append(str(mark.kwargs.get("reason", "")))
    return reasons


def _prepare_provider_neutral_live_env(monkeypatch, tmp_path: Path) -> Path:
    config_path = tmp_path / "provider-neutral-config.yaml"
    _write_provider_neutral_config(config_path)
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "file")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-azure-test")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("CI", raising=False)
    return config_path


def test_create_deerflow_agent_live_skip_uses_active_model_config(monkeypatch, tmp_path):
    _prepare_provider_neutral_live_env(monkeypatch, tmp_path)

    module = _load_test_module("test_create_deerflow_agent_live.py")

    assert _active_skip_reasons(module.test_minimal_agent_responds) == []


def test_client_e2e_skip_and_config_use_active_model_config(monkeypatch, tmp_path):
    _prepare_provider_neutral_live_env(monkeypatch, tmp_path)

    module = _load_test_module("test_client_e2e.py")

    assert _active_skip_reasons(module.TestBasicChat.__dict__["test_basic_chat"]) == []
    assert module._make_e2e_config().models[0].name == "azure-live"
    assert getattr(module._make_e2e_config().models[0], "api_key") == "sk-azure-test"


def test_create_deerflow_agent_live_model_uses_project_factory(monkeypatch, tmp_path):
    _prepare_provider_neutral_live_env(monkeypatch, tmp_path)
    module = _load_test_module("test_create_deerflow_agent_live.py")

    sentinel = object()
    calls: list[dict[str, object]] = []

    import langchain_openai

    import deerflow.models

    class ForbiddenChatOpenAI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("direct ChatOpenAI construction bypasses active AppConfig")

    def fake_create_chat_model(**kwargs):
        calls.append(dict(kwargs))
        return sentinel

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", ForbiddenChatOpenAI)
    monkeypatch.setattr(deerflow.models, "create_chat_model", fake_create_chat_model)

    assert module._make_model() is sentinel
    assert len(calls) == 1
    assert calls[0]["thinking_enabled"] is False
    assert calls[0]["attach_tracing"] is False
    assert calls[0]["app_config"].models[0].name == "azure-live"
