from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent


def _issues_for_file(path: Path, source: str) -> list[str]:
    required_markers = _required_live_gate_markers(path, source)
    if not required_markers:
        return []

    actual_markers = _pytest_markers(source)
    missing = sorted(required_markers - actual_markers)
    if not missing:
        return []
    return [f"{path} requires {', '.join(missing)} markers"]


def _required_live_gate_markers(path: Path, source: str) -> set[str]:
    required: set[str] = set()
    name = path.name

    if name.endswith("_live.py") or name.endswith("_remote_live.py"):
        required.add("live")

    if _uses_env_var(source, "DEER_FLOW_RUN_LIVE_AIO_SANDBOX"):
        required.add("live")

    if _uses_env_var(source, "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX"):
        required.update({"live", "remote_live"})

    if "ONEAPI_E2E" in source or "Real-LLM" in source or _uses_exact_name(source, "_requires_llm_skip"):
        required.update({"live", "requires_llm"})

    if name.endswith("_live.py") and "OPENAI_API_KEY" in source:
        required.add("requires_llm")

    if "docker info" in source or "Requires: Docker running locally" in source:
        required.update({"live", "docker_live"})

    return required


def _uses_env_var(source: str, name: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if _is_os_getenv_call(node, name) or _is_os_environ_lookup(node, name):
            return True
    return False


def _uses_exact_name(source: str, name: str) -> bool:
    tree = ast.parse(source)
    return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(tree))


def _is_os_getenv_call(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr in {"getenv", "get"}
        and isinstance(func.value, ast.Name | ast.Attribute)
    ):
        return False
    if isinstance(func.value, ast.Name):
        owner_matches = func.value.id == "os" and func.attr == "getenv"
    else:
        owner_matches = _is_os_environ_attribute(func.value) and func.attr == "get"
    if not owner_matches or not node.args:
        return False
    first_arg = node.args[0]
    return isinstance(first_arg, ast.Constant) and first_arg.value == name


def _is_os_environ_lookup(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    if not _is_os_environ_attribute(node.value):
        return False
    key = node.slice
    return isinstance(key, ast.Constant) and key.value == name


def _is_os_environ_attribute(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"


def _pytest_markers(source: str) -> set[str]:
    tree = ast.parse(source)
    markers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if not isinstance(value, ast.Attribute) or value.attr != "mark":
            continue
        root = value.value
        if isinstance(root, ast.Name) and root.id == "pytest":
            markers.add(node.attr)
    if _uses_shared_requires_llm_helper(tree):
        markers.update({"live", "requires_llm"})
    return markers


def _uses_shared_requires_llm_helper(tree: ast.AST) -> bool:
    helper_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "support.live_gate_readiness":
            for alias in node.names:
                if alias.name == "mark_requires_llm":
                    helper_names.add(alias.asname or alias.name)

    if not helper_names:
        return False

    decorator_names = set(helper_names)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Name) or node.value.id not in helper_names:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                decorator_names.add(target.id)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id in decorator_names:
                return True
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name) and decorator.func.id in decorator_names:
                return True
    return False


def test_live_gate_marker_detector_flags_missing_required_markers():
    source = '''
import os

import pytest


def test_remote_smoke():
    if os.getenv("DEER_FLOW_RUN_REMOTE_AIO_SANDBOX") != "1":
        pytest.skip("opt in")
'''

    assert _issues_for_file(Path("test_remote_live_missing.py"), source) == [
        "test_remote_live_missing.py requires live, remote_live markers"
    ]


def test_live_gate_marker_detector_ignores_documented_env_var_strings():
    source = '''
def test_preflight_reports_missing_remote_env():
    output = "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX"
    assert "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL" not in output
'''

    assert _issues_for_file(Path("test_stateless_live_gate_check.py"), source) == []


def test_live_gate_marker_detector_recognizes_shared_requires_llm_helper():
    source = '''
from support.live_gate_readiness import mark_requires_llm

requires_llm = mark_requires_llm


@requires_llm
def test_real_llm_flow():
    pass
'''

    assert _pytest_markers(source) >= {"live", "requires_llm"}


def test_live_gate_marker_detector_does_not_count_unused_shared_helper_import():
    source = '''
from support.live_gate_readiness import mark_requires_llm


def test_regular_flow():
    pass
'''

    assert _pytest_markers(source) == set()


def test_live_gate_marker_detector_does_not_treat_helper_name_as_legacy_skip_var():
    source = '''
def test_patches_readiness_helper(monkeypatch):
    monkeypatch.setattr(live_gate_readiness, "requires_llm_skip_reason", lambda: None)
'''

    assert _required_live_gate_markers(Path("test_client_live_db_mode_readiness.py"), source) == set()


def test_live_gate_sources_have_required_pytest_markers():
    issues: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        issues.extend(_issues_for_file(path.relative_to(TESTS_ROOT), path.read_text(encoding="utf-8")))

    assert issues == []
