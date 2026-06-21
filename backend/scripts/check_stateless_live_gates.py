"""Preflight and optionally run Harness stateless DB live verification gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from deerflow.persistence.runtime_config.model import RuntimeConfigRow


@dataclass(frozen=True)
class ConfigReadiness:
    config_issues: list[str] = field(default_factory=list)
    missing_env: list[str] = field(default_factory=list)
    checked_env: list[str] = field(default_factory=list)
    mcp_compatibility: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class GateSpec:
    name: str
    description: str
    command: list[str]
    required_env: dict[str, str | None] = field(default_factory=dict)
    one_of_env: tuple[tuple[str, ...], ...] = ()
    manual_prerequisites: tuple[str, ...] = ()
    requires_model_config: bool = False
    requires_mcp_stateless_config: bool = False


GATE_SPECS: dict[str, GateSpec] = {
    "remote_live": GateSpec(
        name="remote_live",
        description="Remote provisioner/K8s AIO runtime-context materialization smoke",
        command=["uv", "--directory", "backend", "run", "pytest", "tests/test_aio_sandbox_remote_live.py", "-q"],
        required_env={
            "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX": "1",
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": None,
        },
        one_of_env=(("DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH", "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX"),),
        manual_prerequisites=(
            "Provisioner service is reachable from this host",
            "Selected host path or prefix is valid on the Kubernetes node that runs the sandbox Pod",
        ),
    ),
    "docker_live": GateSpec(
        name="docker_live",
        description="Local Docker-backed AIO and lifecycle live gates",
        command=["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"],
        required_env={"DEER_FLOW_RUN_LIVE_AIO_SANDBOX": "1"},
        manual_prerequisites=("Local Docker daemon is available and can start the configured sandbox image",),
    ),
    "requires_llm": GateSpec(
        name="requires_llm",
        description="Model-backed live client and real-LLM gates",
        command=["uv", "--directory", "backend", "run", "pytest", "-m", "requires_llm", "-q"],
        requires_model_config=True,
        manual_prerequisites=("Gateway model config and required provider credentials are configured",),
    ),
    "mcp_stateless": GateSpec(
        name="mcp_stateless",
        description="Strict stateless MCP compatibility gate",
        command=[
            "uv",
            "--directory",
            "backend",
            "run",
            "python",
            "scripts/check_stateless_live_gates.py",
            "--gate",
            "mcp_stateless",
            "--json",
        ],
        requires_mcp_stateless_config=True,
        manual_prerequisites=(
            "HTTP/SSE MCP servers are remote services or enabled stdio MCP servers are explicitly deployed as sticky, sidecar, or single-node compatible",
        ),
    ),
}


def _selected_gate_names(raw_names: Sequence[str]) -> list[str]:
    if not raw_names or "all" in raw_names:
        return list(GATE_SPECS)
    return list(dict.fromkeys(raw_names))


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _gate_report(spec: GateSpec, env: Mapping[str, str], project_root: Path) -> dict[str, object]:
    missing_env: list[str] = []
    invalid_env: list[dict[str, str]] = []
    checked_env = set(spec.required_env)

    for name, expected in spec.required_env.items():
        value = _env_value(env, name)
        if not value:
            missing_env.append(name)
        elif expected is not None and value != expected:
            invalid_env.append({"name": name, "expected": expected, "actual": value})

    for env_group in spec.one_of_env:
        checked_env.update(env_group)
        if not any(_env_value(env, name) for name in env_group):
            missing_env.append(" or ".join(env_group))

    if spec.name == "remote_live":
        invalid_env.extend(_remote_live_env_issues(env))

    readiness_reports: list[ConfigReadiness] = []
    if spec.requires_model_config:
        readiness_reports.append(_llm_config_readiness(project_root, env))
    if spec.requires_mcp_stateless_config:
        readiness_reports.append(_mcp_stateless_readiness(project_root, env))

    config_issues: list[str] = []
    mcp_compatibility: list[dict[str, object]] = []
    for config_readiness in readiness_reports:
        for name in config_readiness.missing_env:
            if name not in missing_env:
                missing_env.append(name)
        checked_env.update(config_readiness.checked_env)
        config_issues.extend(config_readiness.config_issues)
        mcp_compatibility.extend(config_readiness.mcp_compatibility)
    ready_to_invoke = not missing_env and not invalid_env and not config_issues
    report = {
        "name": spec.name,
        "description": spec.description,
        "ready_to_invoke": ready_to_invoke,
        "command": spec.command,
        "missing_env": missing_env,
        "invalid_env": invalid_env,
        "config_issues": config_issues,
        "checked_env": sorted(checked_env),
        "manual_prerequisites": list(spec.manual_prerequisites),
    }
    if mcp_compatibility:
        report["mcp_compatibility"] = mcp_compatibility
    return report


def _env_value(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip()


def _invalid_env(name: str, *, expected: str, actual: str) -> dict[str, str]:
    return {"name": name, "expected": expected, "actual": actual}


def _remote_live_env_issues(env: Mapping[str, str]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    provisioner_url = _env_value(env, "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL")
    if provisioner_url:
        parsed = urlparse(provisioner_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append(
                _invalid_env(
                    "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL",
                    expected="http:// or https:// URL",
                    actual=provisioner_url,
                )
            )

    host_path = _env_value(env, "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH")
    host_path_prefix = _env_value(env, "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX")
    if host_path:
        if not host_path.startswith("/"):
            issues.append(
                _invalid_env(
                    "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH",
                    expected="absolute host path",
                    actual=host_path,
                )
            )
    elif host_path_prefix and not host_path_prefix.startswith("/"):
        issues.append(
            _invalid_env(
                "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX",
                expected="absolute host path",
                actual=host_path_prefix,
            )
        )

    container_path = _env_value(env, "DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH")
    if container_path and not container_path.startswith("/"):
        issues.append(
            _invalid_env(
                "DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH",
                expected="absolute container path",
                actual=container_path,
            )
        )

    ready_timeout = _env_value(env, "DEER_FLOW_REMOTE_AIO_READY_TIMEOUT")
    if ready_timeout:
        try:
            timeout_seconds = int(ready_timeout)
        except ValueError:
            timeout_seconds = 0
        if timeout_seconds <= 0:
            issues.append(
                _invalid_env(
                    "DEER_FLOW_REMOTE_AIO_READY_TIMEOUT",
                    expected="positive integer seconds",
                    actual=ready_timeout,
                )
            )

    return issues


def _llm_config_readiness(project_root: Path, env: Mapping[str, str]) -> ConfigReadiness:
    source_mode = env.get("DEER_FLOW_CONFIG_SOURCE", "file").strip().lower() or "file"
    if source_mode == "db":
        return _db_llm_config_readiness(env)
    if source_mode != "file":
        return ConfigReadiness(config_issues=[f"DEER_FLOW_CONFIG_SOURCE must be 'file' or 'db', got {source_mode!r}"])
    return _file_llm_config_readiness(project_root, env)


def _mcp_stateless_readiness(project_root: Path, env: Mapping[str, str]) -> ConfigReadiness:
    source_mode = env.get("DEER_FLOW_CONFIG_SOURCE", "file").strip().lower() or "file"
    if source_mode == "db":
        return _db_mcp_stateless_readiness(env)
    if source_mode != "file":
        return ConfigReadiness(config_issues=[f"DEER_FLOW_CONFIG_SOURCE must be 'file' or 'db', got {source_mode!r}"])
    return _file_mcp_stateless_readiness(project_root, env)


def _file_llm_config_readiness(project_root: Path, env: Mapping[str, str]) -> ConfigReadiness:
    explicit_config_path = _env_value(env, "DEER_FLOW_CONFIG_PATH")
    config_path = Path(explicit_config_path) if explicit_config_path else _project_config_path(project_root, env)
    if isinstance(config_path, ConfigReadiness):
        return config_path
    if not config_path.is_file():
        if explicit_config_path:
            return ConfigReadiness(config_issues=[f"DEER_FLOW_CONFIG_PATH file not found: {config_path}"])
        if _env_value(env, "DEER_FLOW_PROJECT_ROOT"):
            return ConfigReadiness(config_issues=[f"DEER_FLOW_PROJECT_ROOT config.yaml not found: {config_path}"])
        return ConfigReadiness(config_issues=["config.yaml not found"])
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return ConfigReadiness(config_issues=[f"{config_path} is invalid YAML: {exc}"])
    source_label = _file_config_source_label(config_path, env, explicit_config_path=explicit_config_path)
    return _model_payload_readiness(payload, source_label=source_label, env=env)


def _file_mcp_stateless_readiness(project_root: Path, env: Mapping[str, str]) -> ConfigReadiness:
    explicit_config_path = _env_value(env, "DEER_FLOW_EXTENSIONS_CONFIG_PATH")
    checked_env = ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] if explicit_config_path else []
    if explicit_config_path:
        config_path = Path(explicit_config_path)
        if not config_path.is_file():
            return ConfigReadiness(
                config_issues=[f"DEER_FLOW_EXTENSIONS_CONFIG_PATH file not found: {config_path}"],
                checked_env=checked_env,
            )
    else:
        config_path = None
        for name in ("extensions_config.json", "mcp_config.json"):
            candidate = project_root / name
            if candidate.is_file():
                config_path = candidate
                break
        if config_path is None:
            return _mcp_payload_readiness({"mcpServers": {}}, source_label="extensions config", checked_env=checked_env)

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ConfigReadiness(config_issues=[f"{config_path} is invalid JSON: {exc}"], checked_env=checked_env)
    return _mcp_payload_readiness(payload, source_label=str(config_path), checked_env=checked_env)


def _project_config_path(project_root: Path, env: Mapping[str, str]) -> Path | ConfigReadiness:
    explicit_project_root = _env_value(env, "DEER_FLOW_PROJECT_ROOT")
    if not explicit_project_root:
        return project_root / "config.yaml"

    resolved_project_root = Path(explicit_project_root).resolve()
    if not resolved_project_root.exists():
        return ConfigReadiness(
            config_issues=[
                "DEER_FLOW_PROJECT_ROOT is set to "
                f"{explicit_project_root!r}, but the resolved path {str(resolved_project_root)!r} does not exist."
            ]
        )
    if not resolved_project_root.is_dir():
        return ConfigReadiness(
            config_issues=[
                "DEER_FLOW_PROJECT_ROOT is set to "
                f"{explicit_project_root!r}, but the resolved path {str(resolved_project_root)!r} is not a directory."
            ]
        )
    return resolved_project_root / "config.yaml"


def _file_config_source_label(config_path: Path, env: Mapping[str, str], *, explicit_config_path: str) -> str:
    if explicit_config_path:
        return f"DEER_FLOW_CONFIG_PATH {config_path}"
    if _env_value(env, "DEER_FLOW_PROJECT_ROOT"):
        return f"DEER_FLOW_PROJECT_ROOT {config_path}"
    return "config.yaml"


def _db_llm_config_readiness(env: Mapping[str, str]) -> ConfigReadiness:
    database_url = env.get("DEER_FLOW_DATABASE_URL", "").strip()
    if not database_url:
        return ConfigReadiness(
            config_issues=["DEER_FLOW_DATABASE_URL is required when DEER_FLOW_CONFIG_SOURCE=db"],
            checked_env=["DEER_FLOW_DATABASE_URL"],
        )
    try:
        engine = create_engine(_sync_sqlalchemy_url(database_url))
        try:
            with Session(engine) as session:
                row = session.get(RuntimeConfigRow, "app")
                if row is None:
                    return ConfigReadiness(
                        config_issues=["DB runtime config key 'app' not found"],
                        checked_env=["DEER_FLOW_DATABASE_URL"],
                    )
                payload = row.payload_json
        finally:
            engine.dispose()
    except Exception as exc:
        return ConfigReadiness(
            config_issues=[f"DB runtime config could not be loaded: {exc}"],
            checked_env=["DEER_FLOW_DATABASE_URL"],
        )
    readiness = _model_payload_readiness(payload, source_label="DB runtime config key 'app'", env=env)
    return ConfigReadiness(
        config_issues=readiness.config_issues,
        missing_env=readiness.missing_env,
        checked_env=sorted({"DEER_FLOW_DATABASE_URL", *readiness.checked_env}),
    )


def _db_mcp_stateless_readiness(env: Mapping[str, str]) -> ConfigReadiness:
    from deerflow.persistence.mcp.model import McpServerRow

    database_url = env.get("DEER_FLOW_DATABASE_URL", "").strip()
    if not database_url:
        return ConfigReadiness(
            config_issues=["DEER_FLOW_DATABASE_URL is required when DEER_FLOW_CONFIG_SOURCE=db"],
            checked_env=["DEER_FLOW_DATABASE_URL"],
        )
    try:
        engine = create_engine(_sync_sqlalchemy_url(database_url))
        try:
            with Session(engine) as session:
                rows = list(session.query(McpServerRow).order_by(McpServerRow.name.asc()).all())
                payload = {
                    "mcpServers": {
                        row.name: {
                            **(row.extra_json or {}),
                            "enabled": row.enabled,
                            "type": row.transport_type,
                            "command": row.command,
                            "args": row.args_json or [],
                            "url": row.url,
                            "env": row.env_json or {},
                            "headers": row.headers_json or {},
                            "oauth": row.oauth_json,
                            "description": row.description,
                        }
                        for row in rows
                    }
                }
        finally:
            engine.dispose()
    except Exception as exc:
        return ConfigReadiness(
            config_issues=[f"DB MCP config could not be loaded: {exc}"],
            checked_env=["DEER_FLOW_DATABASE_URL"],
        )
    readiness = _mcp_payload_readiness(payload, source_label="DB mcp_servers", checked_env=["DEER_FLOW_DATABASE_URL"])
    return readiness


def _sync_sqlalchemy_url(url: str) -> str:
    from deerflow.config.bootstrap import database_config_from_url

    config = database_config_from_url(url)
    if config.backend == "sqlite":
        return config.app_sqlalchemy_url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    if config.backend == "postgres":
        async_url = config.app_sqlalchemy_url
        if async_url.startswith("postgresql+asyncpg://"):
            return async_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        if async_url.startswith("postgresql://"):
            return async_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return async_url
    return url


def _model_payload_issues(payload: object, *, source_label: str) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{source_label} top-level value is not an object"]
    models = payload.get("models")
    if not models:
        return [f"{source_label} has no configured models"]
    if not isinstance(models, list):
        return [f"{source_label} models must be a list"]
    if not any(isinstance(model, dict) and model.get("name") and model.get("use") and model.get("model") for model in models):
        return [f"{source_label} has no valid model entries"]
    return []


def _model_payload_readiness(payload: object, *, source_label: str, env: Mapping[str, str]) -> ConfigReadiness:
    config_issues = _model_payload_issues(payload, source_label=source_label)
    if config_issues:
        return ConfigReadiness(config_issues=config_issues)
    env_refs = _model_payload_env_references(payload)
    return ConfigReadiness(
        missing_env=sorted(name for name in env_refs if not env.get(name)),
        checked_env=sorted(env_refs),
    )


_STATELESS_MCP_STDIO_RUNTIME_MODES = {"sticky", "sidecar", "single-node"}


def _mcp_payload_readiness(
    payload: object,
    *,
    source_label: str,
    checked_env: list[str] | None = None,
) -> ConfigReadiness:
    from deerflow.config.extensions_config import ExtensionsConfig

    if not isinstance(payload, dict):
        return ConfigReadiness(
            config_issues=[f"{source_label} top-level value is not an object"],
            checked_env=checked_env or [],
        )
    try:
        config = ExtensionsConfig.model_validate(payload)
    except Exception as exc:
        return ConfigReadiness(
            config_issues=[f"{source_label} is invalid extensions config: {exc}"],
            checked_env=checked_env or [],
        )

    config_issues: list[str] = []
    compatibility: list[dict[str, object]] = []
    for name, server in sorted(config.mcp_servers.items()):
        transport = (server.type or "stdio").strip().lower()
        enabled = bool(server.enabled)
        runtime_mode = _mcp_stateless_runtime_mode(server)
        if not enabled:
            compatibility.append(
                {
                    "name": name,
                    "enabled": False,
                    "transport": transport,
                    "strict_stateless": True,
                    "runtime_mode": "disabled",
                }
            )
            continue
        if transport in {"http", "sse"}:
            compatibility.append(
                {
                    "name": name,
                    "enabled": True,
                    "transport": transport,
                    "strict_stateless": True,
                    "runtime_mode": "remote",
                }
            )
            continue
        if transport == "stdio" and runtime_mode in _STATELESS_MCP_STDIO_RUNTIME_MODES:
            compatibility.append(
                {
                    "name": name,
                    "enabled": True,
                    "transport": transport,
                    "strict_stateless": True,
                    "runtime_mode": runtime_mode,
                }
            )
            continue

        compatibility.append(
            {
                "name": name,
                "enabled": True,
                "transport": transport,
                "strict_stateless": False,
                "runtime_mode": runtime_mode,
            }
        )
        if transport == "stdio":
            config_issues.append(
                f"enabled stdio MCP server '{name}' is not strict-stateless compatible; "
                "set stateless.runtime_mode to sticky, sidecar, or single-node"
            )
        else:
            config_issues.append(f"enabled MCP server '{name}' uses unsupported transport {transport!r} for strict stateless gate")

    return ConfigReadiness(
        config_issues=config_issues,
        checked_env=checked_env or [],
        mcp_compatibility=compatibility,
    )


def _mcp_stateless_runtime_mode(server: object) -> str | None:
    model_extra = getattr(server, "model_extra", None) or {}
    stateless_config = model_extra.get("stateless")
    if isinstance(stateless_config, Mapping):
        value = stateless_config.get("runtime_mode") or stateless_config.get("runtimeMode") or stateless_config.get("mode")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    for key in ("stateless_runtime", "statelessRuntime", "runtime_mode", "runtimeMode"):
        value = model_extra.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _model_payload_missing_env(payload: object, env: Mapping[str, str]) -> list[str]:
    return sorted(name for name in _model_payload_env_references(payload) if not env.get(name))


def _model_payload_env_references(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    models = payload.get("models")
    if not isinstance(models, list):
        return set()
    refs: set[str] = set()
    for model in models:
        if isinstance(model, Mapping):
            refs.update(_env_references(model))
    return refs


def _env_references(value: object) -> set[str]:
    if isinstance(value, str):
        env_name = value[1:] if value.startswith("$") else ""
        return {env_name} if env_name else set()
    if isinstance(value, Mapping):
        refs: set[str] = set()
        for nested in value.values():
            refs.update(_env_references(nested))
        return refs
    if isinstance(value, list):
        refs: set[str] = set()
        for item in value:
            refs.update(_env_references(item))
        return refs
    return set()


def build_gate_report(
    gate_names: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> dict[str, object]:
    effective_env = os.environ if env is None else env
    effective_project_root = _default_project_root() if project_root is None else Path(project_root)
    selected = _selected_gate_names(gate_names or [])
    gates = [_gate_report(GATE_SPECS[name], effective_env, effective_project_root) for name in selected]
    return {
        "ok": all(bool(gate["ready_to_invoke"]) for gate in gates),
        "gates": gates,
    }


def _write_text_report(report: dict[str, object], stdout: TextIO) -> None:
    print("Harness stateless DB live gate preflight", file=stdout)
    print(f"ok: {str(report['ok']).lower()}", file=stdout)
    for gate in report["gates"]:
        assert isinstance(gate, dict)
        print("", file=stdout)
        print(f"[{gate['name']}]", file=stdout)
        print(f"ready_to_invoke: {str(gate['ready_to_invoke']).lower()}", file=stdout)
        print("command: " + " ".join(str(part) for part in gate["command"]), file=stdout)
        if gate["missing_env"]:
            print("missing_env:", file=stdout)
            for name in gate["missing_env"]:
                print(f"  - {name}", file=stdout)
        if gate["invalid_env"]:
            print("invalid_env:", file=stdout)
            for item in gate["invalid_env"]:
                print(f"  - {item['name']}: expected {item['expected']}, got {item['actual']}", file=stdout)
        if gate["config_issues"]:
            print("config_issues:", file=stdout)
            for item in gate["config_issues"]:
                print(f"  - {item}", file=stdout)
        if gate.get("mcp_compatibility"):
            print("mcp_compatibility:", file=stdout)
            for item in gate["mcp_compatibility"]:
                print(
                    "  - "
                    f"{item['name']}: transport={item['transport']} "
                    f"enabled={str(item['enabled']).lower()} "
                    f"strict_stateless={str(item['strict_stateless']).lower()} "
                    f"runtime_mode={item['runtime_mode']}",
                    file=stdout,
                )
        if gate["manual_prerequisites"]:
            print("manual_prerequisites:", file=stdout)
            for item in gate["manual_prerequisites"]:
                print(f"  - {item}", file=stdout)


def _execute_commands(
    report: dict[str, object],
    runner,
    *,
    evidence_log_dir: str | Path | None = None,
    evidence_log_path_prefix: str | Path | None = None,
) -> tuple[int, list[dict[str, object]]]:
    executions: list[dict[str, object]] = []
    if not report["ok"]:
        return 2, executions
    log_root = Path(evidence_log_dir) if evidence_log_dir else None
    log_path_prefix = Path(evidence_log_path_prefix) if evidence_log_path_prefix else log_root
    if log_root is not None:
        log_root.mkdir(parents=True, exist_ok=True)
    for gate in report["gates"]:
        assert isinstance(gate, dict)
        command = [str(part) for part in gate["command"]]
        result = runner(command)
        exit_code = int(result)
        execution = {"gate": gate["name"], "command": command, "exit_code": exit_code}
        if log_root is not None:
            assert log_path_prefix is not None
            _write_execution_logs(
                log_root,
                log_path_prefix,
                len(executions) + 1,
                str(gate["name"]),
                result,
                execution,
            )
        executions.append(execution)
        if exit_code != 0:
            return exit_code, executions
    return 0, executions


def _run_commands(report: dict[str, object], runner) -> int:
    exit_code, _executions = _execute_commands(report, runner)
    return exit_code


def _write_execution_logs(
    log_root: Path,
    log_path_prefix: Path,
    index: int,
    gate_name: str,
    result: object,
    execution: dict[str, object],
) -> None:
    safe_gate = _safe_log_name(gate_name)
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    if isinstance(stdout, str):
        stdout_name = f"{index:03d}-{safe_gate}.stdout.log"
        stdout_path = log_root / stdout_name
        stdout_bytes = stdout.encode("utf-8")
        stdout_path.write_bytes(stdout_bytes)
        execution["stdout_log_path"] = str(log_path_prefix / stdout_name)
        execution["stdout_log_sha256"] = hashlib.sha256(stdout_bytes).hexdigest()
        execution["stdout_log_bytes"] = len(stdout_bytes)
    if isinstance(stderr, str):
        stderr_name = f"{index:03d}-{safe_gate}.stderr.log"
        stderr_path = log_root / stderr_name
        stderr_bytes = stderr.encode("utf-8")
        stderr_path.write_bytes(stderr_bytes)
        execution["stderr_log_path"] = str(log_path_prefix / stderr_name)
        execution["stderr_log_sha256"] = hashlib.sha256(stderr_bytes).hexdigest()
        execution["stderr_log_bytes"] = len(stderr_bytes)


def _safe_log_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def _execution_log_paths(
    *,
    evidence_path: str | Path | None,
    evidence_log_dir: str | Path | None,
) -> tuple[Path | None, Path | None]:
    if evidence_log_dir is None:
        return None, None

    log_path_prefix = Path(evidence_log_dir)
    if evidence_path and not log_path_prefix.is_absolute():
        return Path(evidence_path).parent / log_path_prefix, log_path_prefix
    return log_path_prefix, log_path_prefix


def _write_evidence_report(
    path: str | Path,
    *,
    report: dict[str, object],
    run_requested: bool,
    executions: list[dict[str, object]],
    overall_exit_code: int,
) -> None:
    evidence_path = Path(path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "cwd": str(Path.cwd()),
        "source": _source_metadata(),
        "preflight": report,
        "selected_gates": [gate["name"] for gate in report["gates"] if isinstance(gate, dict)],
        "run_requested": run_requested,
        "executions": executions,
        "overall_exit_code": overall_exit_code,
    }
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_evidence_report(
    path: str | Path,
    *,
    require_run: bool = False,
    require_logs: bool = False,
) -> dict[str, object]:
    evidence_path = Path(path)
    errors: list[str] = []
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return _evidence_validation_result(errors=[f"evidence file could not be read: {exc}"])
    except json.JSONDecodeError as exc:
        return _evidence_validation_result(errors=[f"evidence file is not valid JSON: {exc}"])

    if not isinstance(payload, dict):
        return _evidence_validation_result(errors=["evidence top-level value must be an object"])

    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        errors.append("schema_version must be 1")

    _require_type(payload, "generated_at_utc", str, errors)
    _require_type(payload, "cwd", str, errors)
    _require_type(payload, "source", dict, errors)
    preflight = _require_type(payload, "preflight", dict, errors)
    selected_gates = _require_type(payload, "selected_gates", list, errors)
    run_requested = _require_type(payload, "run_requested", bool, errors)
    executions = _require_type(payload, "executions", list, errors)
    overall_exit_code = _require_type(payload, "overall_exit_code", int, errors)

    if isinstance(selected_gates, list) and not all(isinstance(gate, str) for gate in selected_gates):
        errors.append("selected_gates must contain only strings")
    if isinstance(overall_exit_code, bool):
        errors.append("overall_exit_code must be an integer")
        overall_exit_code = None
    _validate_evidence_execution_exit_codes(
        executions=executions,
        overall_exit_code=overall_exit_code,
        errors=errors,
    )
    if require_run:
        _validate_evidence_run_coverage(
            preflight=preflight,
            selected_gates=selected_gates,
            run_requested=run_requested,
            executions=executions,
            errors=errors,
        )
    if require_logs:
        _validate_evidence_execution_logs(executions=executions, evidence_dir=evidence_path.parent, errors=errors)

    return _evidence_validation_result(
        errors=errors,
        schema_version=schema_version if isinstance(schema_version, int) and not isinstance(schema_version, bool) else None,
        selected_gates=selected_gates if isinstance(selected_gates, list) else [],
        overall_exit_code=overall_exit_code if isinstance(overall_exit_code, int) else None,
    )


def _validate_evidence_execution_exit_codes(
    *,
    executions: object,
    overall_exit_code: object,
    errors: list[str],
) -> None:
    if not isinstance(executions, list) or not isinstance(overall_exit_code, int):
        return

    failed_executions: list[str] = []
    for execution in executions:
        if not isinstance(execution, Mapping):
            errors.append("executions must contain only objects")
            continue

        gate = execution.get("gate")
        if not isinstance(gate, str):
            errors.append("execution gate must be a string")
            continue

        exit_code = execution.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            errors.append(f"execution exit_code must be an integer for gate {gate}")
            continue

        if overall_exit_code == 0 and exit_code != 0:
            failed_executions.append(f"{gate}={exit_code}")

    if failed_executions:
        errors.append(
            "overall_exit_code 0 conflicts with failed execution exit_code: "
            + ", ".join(failed_executions)
        )


def _validate_evidence_run_coverage(
    *,
    preflight: object,
    selected_gates: object,
    run_requested: object,
    executions: object,
    errors: list[str],
) -> None:
    if run_requested is not True:
        errors.append("run_requested must be true when --require-run is used")
    if not isinstance(selected_gates, list) or not all(isinstance(gate, str) for gate in selected_gates):
        return
    if not selected_gates:
        errors.append("selected_gates must contain at least one gate when --require-run is used")
    duplicate_selected_gates = _duplicate_strings(selected_gates)
    if duplicate_selected_gates:
        errors.append(
            "selected_gates must contain each gate at most once when --require-run is used: duplicate "
            + ", ".join(duplicate_selected_gates)
        )
    unknown_gates = sorted(set(selected_gates) - set(GATE_SPECS))
    if unknown_gates:
        errors.append("selected_gates contains unknown gates when --require-run is used: " + ", ".join(unknown_gates))
    if isinstance(preflight, Mapping) and preflight.get("ok") is not True:
        errors.append("preflight.ok must be true when --require-run is used")
    if not isinstance(executions, list):
        return
    executed_gates = {
        execution.get("gate")
        for execution in executions
        if isinstance(execution, Mapping) and isinstance(execution.get("gate"), str)
    }
    selected_gate_names = set(selected_gates)
    extra_gates = sorted(executed_gates - selected_gate_names)
    if extra_gates:
        errors.append(
            "executions contain gates that were not selected when --require-run is used: "
            + ", ".join(extra_gates)
        )
    duplicate_gates = _duplicate_execution_gates(executions)
    if duplicate_gates:
        errors.append(
            "executions must contain exactly one record per selected gate when --require-run is used: duplicate "
            + ", ".join(duplicate_gates)
        )
    missing_gates = sorted(set(selected_gates) - executed_gates)
    if missing_gates:
        errors.append(
            "executions must include every selected gate when --require-run is used: missing "
            + ", ".join(missing_gates)
        )
    _validate_evidence_execution_commands(preflight=preflight, selected_gates=selected_gates, executions=executions, errors=errors)


def _duplicate_execution_gates(executions: list[object]) -> list[str]:
    return _duplicate_strings(
        [
            gate
            for execution in executions
            if isinstance(execution, Mapping) and isinstance((gate := execution.get("gate")), str)
        ]
    )


def _duplicate_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _validate_evidence_execution_commands(
    *,
    preflight: object,
    selected_gates: list[object],
    executions: list[object],
    errors: list[str],
) -> None:
    expected_commands = _preflight_gate_commands(preflight=preflight, selected_gates=selected_gates, errors=errors)
    if not expected_commands:
        return

    for execution in executions:
        if not isinstance(execution, Mapping):
            continue
        gate = execution.get("gate")
        if not isinstance(gate, str) or gate not in expected_commands:
            continue
        command = execution.get("command")
        if not _is_string_list(command):
            errors.append(f"execution command must be a string list for gate {gate}")
            continue
        if command != expected_commands[gate]:
            errors.append(f"execution command must match preflight command for gate {gate}")


def _preflight_gate_commands(
    *,
    preflight: object,
    selected_gates: list[object],
    errors: list[str],
) -> dict[str, list[str]]:
    if not isinstance(preflight, Mapping):
        return {}
    selected_gate_names = {gate for gate in selected_gates if isinstance(gate, str)}
    gates = preflight.get("gates")
    if not isinstance(gates, list):
        errors.append("preflight.gates must be a list when --require-run is used")
        return {}

    commands: dict[str, list[str]] = {}
    seen_gate_names: set[str] = set()
    selected_seen_gate_names: set[str] = set()
    duplicate_selected_gate_names: set[str] = set()
    for gate in gates:
        if not isinstance(gate, Mapping):
            errors.append("preflight.gates must contain only objects when --require-run is used")
            continue
        gate_name = gate.get("name")
        if not isinstance(gate_name, str):
            errors.append("preflight gate name must be a string when --require-run is used")
            continue
        seen_gate_names.add(gate_name)
        if gate_name not in selected_gate_names:
            continue
        if gate_name in selected_seen_gate_names:
            duplicate_selected_gate_names.add(gate_name)
        selected_seen_gate_names.add(gate_name)
        if gate.get("ready_to_invoke") is not True:
            errors.append(f"preflight gate {gate_name} ready_to_invoke must be true when --require-run is used")
        command = gate.get("command")
        if not _is_string_list(command):
            errors.append(f"preflight command must be a string list for selected gate {gate_name}")
            continue
        commands[gate_name] = command

    missing_gate_names = sorted(selected_gate_names - seen_gate_names)
    if missing_gate_names:
        errors.append(
            "preflight.gates must include every selected gate when --require-run is used: missing "
            + ", ".join(missing_gate_names)
        )
    if duplicate_selected_gate_names:
        errors.append(
            "preflight.gates must contain each selected gate at most once when --require-run is used: duplicate "
            + ", ".join(sorted(duplicate_selected_gate_names))
        )
    return commands


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_evidence_execution_logs(*, executions: object, evidence_dir: Path, errors: list[str]) -> None:
    if not isinstance(executions, list):
        return
    for execution in executions:
        if not isinstance(execution, Mapping):
            continue
        gate = execution.get("gate")
        gate_name = gate if isinstance(gate, str) else "<unknown>"
        for key in ("stdout_log_path", "stderr_log_path"):
            log_path = execution.get(key)
            if not isinstance(log_path, str):
                errors.append(f"execution {key} must be a string for gate {gate_name} when --require-logs is used")
                continue
            resolved_log_path = _resolve_evidence_log_path(log_path, evidence_dir)
            if not resolved_log_path.is_file():
                errors.append(f"execution {key} file does not exist for gate {gate_name}: {log_path}")
                continue
            _validate_evidence_execution_log_integrity(
                execution=execution,
                gate_name=gate_name,
                key=key,
                resolved_log_path=resolved_log_path,
                errors=errors,
            )


def _validate_evidence_execution_log_integrity(
    *,
    execution: Mapping[str, object],
    gate_name: str,
    key: str,
    resolved_log_path: Path,
    errors: list[str],
) -> None:
    field_prefix = key.removesuffix("_path")
    expected_sha256 = execution.get(f"{field_prefix}_sha256")
    expected_bytes = execution.get(f"{field_prefix}_bytes")
    if not _is_sha256_hex_digest(expected_sha256):
        errors.append(f"execution {field_prefix}_sha256 must be a sha256 hex digest for gate {gate_name}")
        return
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
        errors.append(f"execution {field_prefix}_bytes must be a non-negative integer for gate {gate_name}")
        return

    try:
        log_bytes = resolved_log_path.read_bytes()
    except OSError as exc:
        errors.append(f"execution {key} file could not be read for gate {gate_name}: {exc}")
        return
    if len(log_bytes) != expected_bytes:
        errors.append(f"execution {field_prefix}_bytes does not match file size for gate {gate_name}")
    if hashlib.sha256(log_bytes).hexdigest() != expected_sha256:
        errors.append(f"execution {field_prefix}_sha256 does not match file content for gate {gate_name}")


def _is_sha256_hex_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _resolve_evidence_log_path(log_path: str, evidence_dir: Path) -> Path:
    path = Path(log_path)
    if path.is_absolute():
        return path
    return evidence_dir / path


def _require_type(payload: Mapping[str, object], key: str, expected_type: type, errors: list[str]) -> object | None:
    value = payload.get(key)
    if not isinstance(value, expected_type):
        errors.append(f"{key} must be {expected_type.__name__}")
        return None
    return value


def _evidence_validation_result(
    *,
    errors: list[str],
    schema_version: int | None = None,
    selected_gates: list[object] | None = None,
    overall_exit_code: int | None = None,
) -> dict[str, object]:
    valid = not errors
    return {
        "valid": valid,
        "success": valid and overall_exit_code == 0,
        "schema_version": schema_version,
        "overall_exit_code": overall_exit_code,
        "selected_gates": selected_gates or [],
        "errors": errors,
    }


def _write_evidence_validation_report(validation: dict[str, object], stdout: TextIO) -> None:
    print("Harness stateless DB live gate evidence validation", file=stdout)
    print(f"valid: {str(validation['valid']).lower()}", file=stdout)
    print(f"success: {str(validation['success']).lower()}", file=stdout)
    print(f"schema_version: {validation['schema_version']}", file=stdout)
    print(f"overall_exit_code: {validation['overall_exit_code']}", file=stdout)
    if validation["selected_gates"]:
        print("selected_gates:", file=stdout)
        for gate in validation["selected_gates"]:
            print(f"  - {gate}", file=stdout)
    if validation["errors"]:
        print("errors:", file=stdout)
        for error in validation["errors"]:
            print(f"  - {error}", file=stdout)


def _source_metadata() -> dict[str, object]:
    return {"git": _git_metadata()}


def _git_metadata() -> dict[str, object]:
    try:
        repo_root = _git_output(["rev-parse", "--show-toplevel"])
        head = _git_output(["rev-parse", "HEAD"])
        branch = _git_output(["rev-parse", "--abbrev-ref", "HEAD"])
        status_short = _git_output(["status", "--short"])
    except RuntimeError as exc:
        return {"available": False, "error": str(exc)}

    status_lines = [line for line in status_short.splitlines() if line.strip()]
    return {
        "available": True,
        "repo_root": repo_root,
        "head": head,
        "branch": branch,
        "dirty": bool(status_lines),
        "status_short_count": len(status_lines),
    }


def _git_output(args: Sequence[str]) -> str:
    try:
        result = subprocess.run(["git", *args], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"git {' '.join(args)} failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = f"git {' '.join(args)} exited {result.returncode}"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message)
    return result.stdout.strip()


def _subprocess_runner(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def _subprocess_capture_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runner=None,
    stdout: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Preflight Harness stateless DB live verification gates")
    parser.add_argument("--gate", action="append", choices=[*GATE_SPECS.keys(), "all"], help="Gate to check. Defaults to all gates.")
    parser.add_argument("--json", action="store_true", help="Write machine-readable JSON instead of text.")
    parser.add_argument("--run", action="store_true", help="Run selected pytest commands when preflight is ready.")
    parser.add_argument("--evidence-path", help="Write a JSON evidence report with preflight and optional execution results.")
    parser.add_argument("--evidence-log-dir", help="When running gates, write each executed command stdout/stderr to this directory and record paths in evidence.")
    parser.add_argument("--validate-evidence", help="Validate an existing JSON evidence report and return its success status.")
    parser.add_argument("--require-run", action="store_true", help="When validating evidence, require every selected gate to have an execution record.")
    parser.add_argument("--require-logs", action="store_true", help="When validating evidence, require every execution to reference existing stdout/stderr log files.")
    args = parser.parse_args(argv)

    output = sys.stdout if stdout is None else stdout
    if args.validate_evidence:
        validation = _validate_evidence_report(
            args.validate_evidence,
            require_run=args.require_run,
            require_logs=args.require_logs,
        )
        if args.json:
            print(json.dumps(validation, indent=2, sort_keys=True), file=output)
        else:
            _write_evidence_validation_report(validation, output)
        if not validation["valid"]:
            return 1
        return 0 if validation["success"] else 2

    report = build_gate_report(args.gate, env=env)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True), file=output)
    else:
        _write_text_report(report, output)

    executions: list[dict[str, object]] = []
    if args.run:
        if runner is None:
            command_runner = _subprocess_capture_runner if args.evidence_log_dir else _subprocess_runner
        else:
            command_runner = runner
        evidence_log_dir, evidence_log_path_prefix = _execution_log_paths(
            evidence_path=args.evidence_path,
            evidence_log_dir=args.evidence_log_dir,
        )
        exit_code, executions = _execute_commands(
            report,
            command_runner,
            evidence_log_dir=evidence_log_dir,
            evidence_log_path_prefix=evidence_log_path_prefix,
        )
    else:
        exit_code = 0 if report["ok"] else 2

    if args.evidence_path:
        _write_evidence_report(
            args.evidence_path,
            report=report,
            run_requested=args.run,
            executions=executions,
            overall_exit_code=exit_code,
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
