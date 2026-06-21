from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "backend" / "scripts" / "check_stateless_live_gates.py"


def _load_live_gate_check_module():
    module_name = "_stateless_live_gate_check_for_tests"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def requires_llm_skip_reason(
    *,
    env: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> str | None:
    effective_env = os.environ if env is None else env
    if effective_env.get("CI", "").lower() in {"true", "1"}:
        return "Requires LLM live gate is skipped in CI"

    live_gate_check = _load_live_gate_check_module()
    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env=effective_env,
        project_root=REPO_ROOT if project_root is None else project_root,
    )
    if report["ok"]:
        return None

    gate = report["gates"][0]
    details: list[str] = []
    if gate["missing_env"]:
        details.append("missing_env=" + ",".join(str(item) for item in gate["missing_env"]))
    if gate["invalid_env"]:
        details.append(
            "invalid_env="
            + ",".join(f"{item['name']} expected {item['expected']}" for item in gate["invalid_env"])
        )
    if gate["config_issues"]:
        details.append("config_issues=" + "; ".join(str(item) for item in gate["config_issues"]))
    suffix = "; ".join(details) if details else "preflight did not report ready"
    return f"Requires configured LLM live gate readiness: {suffix}"


def requires_llm_skip_marker() -> pytest.MarkDecorator:
    reason = requires_llm_skip_reason()
    return pytest.mark.skipif(reason is not None, reason=reason or "Requires configured LLM live gate readiness")


def mark_requires_llm(test_func):
    return requires_llm_skip_marker()(pytest.mark.live(pytest.mark.requires_llm(test_func)))


def load_active_app_config_for_requires_llm():
    from deerflow.config.bootstrap import is_db_config_enabled

    if is_db_config_enabled():
        from deerflow.config.app_config import load_and_cache_bootstrap_db_app_config

        asyncio.run(load_and_cache_bootstrap_db_app_config())

    from deerflow.config import get_app_config

    return get_app_config()
