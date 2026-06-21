"""Dry-run import planning for file-backed Harness runtime state.

Usage:
    PYTHONPATH=. python scripts/import_runtime_state_to_db.py [--project-root PATH] [--state-dir PATH] [--skills-root PATH]

The default mode is dry-run inventory only. It does not initialize DB stores or
write any database rows. A later implementation phase can add ``--apply`` on top
of the same inventory report.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import stat
import tempfile
import warnings
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from deerflow.config.database_config import DatabaseConfig

_DEFAULT_DB_SKILL_FILE_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_DB_SKILL_PACKAGE_MAX_BYTES = 50 * 1024 * 1024
_SECRET_FIELD_NAMES = {
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "client_secret",
    "refresh_token",
    "access_token",
    "token",
    "password",
    "secret",
}
_STATELESS_MCP_STDIO_RUNTIME_MODES = {"sticky", "sidecar", "single-node"}


@contextmanager
def _suppress_cli_runtime_noise():
    """Keep the migration CLI's stderr reserved for command failures."""
    app_config_logger = logging.getLogger("deerflow.config.app_config")
    previous_level = app_config_logger.level
    app_config_logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            yield
    finally:
        app_config_logger.setLevel(previous_level)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_error": f"invalid json: {exc}"}
    return data if isinstance(data, dict) else {"_error": "top-level JSON value is not an object"}


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return {"_error": f"invalid yaml: {exc}"}
    return data if isinstance(data, dict) else {"_error": "top-level YAML value is not an object"}


def _memory_shape_error(payload: dict[str, Any]) -> str | None:
    if "version" in payload and not isinstance(payload["version"], str):
        return "version must be a string"
    if "user" in payload and not isinstance(payload["user"], dict):
        return "user must be an object"
    if "history" in payload and not isinstance(payload["history"], dict):
        return "history must be an object"
    if "facts" in payload and not isinstance(payload["facts"], list):
        return "facts must be a list"
    return None


def _agent_config_error(payload: dict[str, Any], *, fallback_name: str) -> str | None:
    from deerflow.config.agents_config import AgentConfig, validate_agent_name

    if error := payload.get("_error"):
        return error
    candidate = dict(payload)
    candidate.setdefault("name", fallback_name)
    try:
        validate_agent_name(candidate.get("name"))
        AgentConfig.model_validate(candidate)
    except Exception as exc:
        return str(exc)
    return None


def _app_config_error(path: str | None) -> str | None:
    from deerflow.config.app_config import AppConfig

    if path is None:
        return None
    payload = _read_yaml(Path(path))
    if error := payload.get("_error"):
        return error
    try:
        AppConfig.from_payload(payload, source_label=path, apply_singletons=False)
    except Exception as exc:
        return str(exc)
    return None


def _extensions_config_error(path: str | None) -> str | None:
    from deerflow.config.extensions_config import ExtensionsConfig

    if path is None:
        return None
    payload = _read_json(Path(path))
    if error := payload.get("_error"):
        return error
    try:
        ExtensionsConfig.model_validate(ExtensionsConfig.resolve_env_variables(payload))
    except Exception as exc:
        return str(exc)
    return None


def _config_report(project_root: Path) -> dict[str, Any]:
    path = project_root / "config.yaml"
    if not path.is_file():
        return {"exists": False, "path": str(path), "top_level_keys": [], "risks": []}
    payload = _read_yaml(path)
    return {
        "exists": True,
        "path": str(path),
        "top_level_keys": sorted(key for key in payload if not key.startswith("_")),
        "risks": _config_secret_risks(payload),
        "error": payload.get("_error"),
    }


def _channel_runtime_report(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "channels" / "runtime-config.json"
    if not path.is_file():
        return {"exists": False, "path": str(path), "providers": [], "risks": [], "error": None}

    payload = _read_json(path)
    providers: list[dict[str, Any]] = []
    risks: list[dict[str, str]] = []
    if "_error" not in payload:
        for name, config in sorted(payload.items()):
            if isinstance(config, dict):
                provider_name = str(name)
                providers.append(
                    {
                        "name": provider_name,
                        "enabled": bool(config.get("enabled", False)),
                        "field_count": len(config),
                    }
                )
                _append_channel_runtime_risks(risks, provider_name=provider_name, values=config)
    return {
        "exists": True,
        "path": str(path),
        "providers": providers,
        "risks": risks,
        "error": payload.get("_error"),
    }


def _extensions_path(project_root: Path) -> Path | None:
    for name in ("extensions_config.json", "mcp_config.json"):
        path = project_root / name
        if path.is_file():
            return path
    return None


def _is_secret_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("$")


def _is_secret_field_name(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_")
    return (
        normalized in _SECRET_FIELD_NAMES
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_api_key")
    )


def _append_secret_value_risk(risks: list[dict[str, str]], *, field: str, value: Any, context: dict[str, str] | None = None) -> None:
    if not value:
        return
    risk_context = context or {}
    if _is_secret_ref(value):
        env_name = str(value)[1:]
        if env_name:
            risks.append({**risk_context, "risk": "env-ref", "field": field, "env": env_name})
    else:
        risks.append({**risk_context, "risk": "resolved-secret", "field": field})


def _config_secret_risks(payload: object) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []

    def visit(value: object, path: str, field_name: str | None = None) -> None:
        if field_name is not None and _is_secret_field_name(field_name):
            _append_secret_value_risk(risks, field=path, value=value)
            return
        if isinstance(value, Mapping):
            for key, nested in value.items():
                key_text = str(key)
                nested_path = f"{path}.{key_text}" if path else key_text
                visit(nested, nested_path, key_text)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, f"{path}[{index}]" if path else f"[{index}]")

    visit(payload, "")
    return risks


def _append_secret_risks(risks: list[dict[str, str]], *, server_name: str, prefix: str, values: dict[str, Any]) -> None:
    for key, value in values.items():
        _append_secret_value_risk(risks, field=f"{prefix}.{key}", value=value, context={"server": server_name})


def _append_channel_runtime_risks(risks: list[dict[str, str]], *, provider_name: str, values: dict[str, Any]) -> None:
    for key, value in values.items():
        key_text = str(key)
        if _is_secret_field_name(key_text):
            _append_secret_value_risk(risks, field=f"{provider_name}.{key_text}", value=value, context={"provider": provider_name})


def _mcp_stateless_runtime_mode(server: dict[str, Any]) -> str | None:
    stateless = server.get("stateless")
    if isinstance(stateless, dict):
        value = stateless.get("runtime_mode") or stateless.get("runtimeMode") or stateless.get("mode")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    for key in ("stateless_runtime", "statelessRuntime", "runtime_mode", "runtimeMode"):
        value = server.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _mcp_compatibility_report(name: str, server: dict[str, Any]) -> dict[str, Any]:
    transport = str(server.get("type") or server.get("transport") or "stdio").strip().lower()
    enabled = bool(server.get("enabled", True))
    runtime_mode = _mcp_stateless_runtime_mode(server)
    if not enabled:
        return {
            "name": name,
            "enabled": False,
            "transport": transport,
            "strict_stateless": True,
            "runtime_mode": "disabled",
        }
    if transport in {"http", "sse"}:
        return {
            "name": name,
            "enabled": True,
            "transport": transport,
            "strict_stateless": True,
            "runtime_mode": "remote",
        }
    if transport == "stdio" and runtime_mode in _STATELESS_MCP_STDIO_RUNTIME_MODES:
        return {
            "name": name,
            "enabled": True,
            "transport": transport,
            "strict_stateless": True,
            "runtime_mode": runtime_mode,
        }
    return {
        "name": name,
        "enabled": True,
        "transport": transport,
        "strict_stateless": False,
        "runtime_mode": runtime_mode,
    }


def _nonnegative_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return max(parsed, 0)


def _db_skill_file_max_bytes() -> int:
    return _nonnegative_int_env("DEER_FLOW_DB_SKILL_FILE_MAX_BYTES", _DEFAULT_DB_SKILL_FILE_MAX_BYTES)


def _db_skill_package_max_bytes() -> int:
    return _nonnegative_int_env("DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES", _DEFAULT_DB_SKILL_PACKAGE_MAX_BYTES)


def _extensions_report(project_root: Path) -> dict[str, Any]:
    path = _extensions_path(project_root)
    if path is None:
        return {
            "exists": False,
            "path": None,
            "mcp_servers": [],
            "mcp_compatibility": [],
            "skill_states": [],
            "risks": [],
        }

    payload = _read_json(path)
    mcp_servers = payload.get("mcpServers", {})
    skills = payload.get("skills", {})
    risks: list[dict[str, str]] = []
    server_reports: list[dict[str, Any]] = []
    compatibility_reports: list[dict[str, Any]] = []

    if isinstance(mcp_servers, dict):
        for name, server in sorted(mcp_servers.items()):
            if not isinstance(server, dict):
                continue
            transport_type = server.get("type") or server.get("transport") or "stdio"
            compatibility = _mcp_compatibility_report(str(name), server)
            compatibility_reports.append(compatibility)
            if compatibility["enabled"] and compatibility["transport"] == "stdio" and not compatibility["strict_stateless"]:
                risks.append({"server": name, "risk": "stdio-mcp-stateful"})
            _append_secret_risks(risks, server_name=name, prefix="env", values=server.get("env") or {})
            _append_secret_risks(risks, server_name=name, prefix="headers", values=server.get("headers") or {})
            oauth = server.get("oauth") or {}
            if isinstance(oauth, dict):
                _append_secret_risks(
                    risks,
                    server_name=name,
                    prefix="oauth",
                    values={key: oauth.get(key) for key in ("client_secret", "refresh_token") if oauth.get(key)},
                )
            server_reports.append(
                {
                    "name": name,
                    "type": transport_type,
                    "enabled": bool(server.get("enabled", True)),
                    "has_env": bool(server.get("env")),
                    "has_headers": bool(server.get("headers")),
                    "has_oauth": bool(server.get("oauth")),
                    "strict_stateless": compatibility["strict_stateless"],
                    "runtime_mode": compatibility["runtime_mode"],
                }
            )

    skill_states = []
    if isinstance(skills, dict):
        for name, state in sorted(skills.items()):
            enabled = True
            if isinstance(state, dict):
                enabled = bool(state.get("enabled", True))
            skill_states.append({"name": name, "enabled": enabled})

    return {
        "exists": True,
        "path": str(path),
        "mcp_servers": server_reports,
        "mcp_compatibility": compatibility_reports,
        "skill_states": skill_states,
        "risks": risks,
        "error": payload.get("_error"),
    }


def _agent_report(agent_dir: Path, *, owner_user_id: str, legacy: bool) -> dict[str, Any] | None:
    config_path = agent_dir / "config.yaml"
    if not config_path.is_file():
        return None
    payload = _read_yaml(config_path)
    return {
        "name": agent_dir.name,
        "owner_user_id": owner_user_id,
        "legacy": legacy,
        "path": str(agent_dir),
        "has_soul": (agent_dir / "SOUL.md").is_file(),
        "has_memory": (agent_dir / "memory.json").is_file(),
        "error": _agent_config_error(payload, fallback_name=agent_dir.name),
    }


def _agents_report(state_dir: Path) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    legacy_root = state_dir / "agents"
    if legacy_root.is_dir():
        for agent_dir in sorted(legacy_root.iterdir()):
            if agent_dir.is_dir():
                report = _agent_report(agent_dir, owner_user_id="default", legacy=True)
                if report is not None:
                    agents.append(report)

    users_root = state_dir / "users"
    if users_root.is_dir():
        for user_dir in sorted(users_root.iterdir()):
            agents_root = user_dir / "agents"
            if not agents_root.is_dir():
                continue
            for agent_dir in sorted(agents_root.iterdir()):
                if agent_dir.is_dir():
                    report = _agent_report(agent_dir, owner_user_id=user_dir.name, legacy=False)
                    if report is not None:
                        agents.append(report)
    return agents


def _user_profile_reports(state_dir: Path) -> list[dict[str, Any]]:
    profile_path = state_dir / "USER.md"
    if not profile_path.is_file():
        return []
    return [
        {
            "owner_user_id": "default",
            "path": str(profile_path),
            "legacy": True,
        }
    ]


def _default_agent_soul_reports(state_dir: Path) -> list[dict[str, Any]]:
    soul_path = state_dir / "SOUL.md"
    if not soul_path.is_file():
        return []
    return [
        {
            "owner_user_id": "default",
            "path": str(soul_path),
            "legacy": True,
        }
    ]


def _memory_report(path: Path, *, owner_user_id: str, agent_scope: str, legacy: bool) -> dict[str, Any]:
    payload = _read_json(path)
    error = payload.get("_error")
    if error is None:
        error = _memory_shape_error(payload)
    return {
        "owner_user_id": owner_user_id,
        "agent_scope": agent_scope,
        "legacy": legacy,
        "path": str(path),
        "error": error,
    }


def _memory_reports(state_dir: Path) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    if (state_dir / "memory.json").is_file():
        memories.append(
            _memory_report(
                state_dir / "memory.json",
                owner_user_id="default",
                agent_scope="",
                legacy=True,
            )
        )

    legacy_agents = state_dir / "agents"
    if legacy_agents.is_dir():
        for agent_dir in sorted(legacy_agents.iterdir()):
            memory_path = agent_dir / "memory.json"
            if memory_path.is_file():
                memories.append(
                    _memory_report(
                        memory_path,
                        owner_user_id="default",
                        agent_scope=agent_dir.name,
                        legacy=True,
                    )
                )

    users_root = state_dir / "users"
    if users_root.is_dir():
        for user_dir in sorted(users_root.iterdir()):
            user_memory = user_dir / "memory.json"
            if user_memory.is_file():
                memories.append(
                    _memory_report(
                        user_memory,
                        owner_user_id=user_dir.name,
                        agent_scope="",
                        legacy=False,
                    )
                )
            agents_root = user_dir / "agents"
            if not agents_root.is_dir():
                continue
            for agent_dir in sorted(agents_root.iterdir()):
                memory_path = agent_dir / "memory.json"
                if memory_path.is_file():
                    memories.append(
                        _memory_report(
                            memory_path,
                            owner_user_id=user_dir.name,
                            agent_scope=agent_dir.name,
                            legacy=False,
                        )
                    )
    return memories


def _skills_report(skills_root: Path) -> list[dict[str, Any]]:
    from deerflow.skills.validation import _validate_skill_frontmatter

    skills: list[dict[str, Any]] = []
    max_file_bytes = _db_skill_file_max_bytes()
    max_package_bytes = _db_skill_package_max_bytes()
    for category in ("public", "custom"):
        category_root = skills_root / category
        if not category_root.is_dir():
            continue
        for skill_dir in sorted(category_root.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            is_valid, message, frontmatter_name = _validate_skill_frontmatter(skill_dir)
            error = None
            if not is_valid:
                error = message
            elif frontmatter_name != skill_dir.name:
                error = f"frontmatter name {frontmatter_name!r} does not match directory name {skill_dir.name!r}"
            support_files = [path for path in skill_dir.rglob("*") if path.is_file() and path.name != "SKILL.md"]
            support_file_bytes = 0
            risks: list[dict[str, Any]] = []
            skill_md_bytes = skill_md.stat().st_size
            for path in support_files:
                size = path.stat().st_size
                support_file_bytes += size
                if size > max_file_bytes:
                    risks.append(
                        {
                            "risk": "skill-file-too-large",
                            "path": str(path),
                            "bytes": size,
                            "max_bytes": max_file_bytes,
                        }
                    )
            package_bytes = skill_md_bytes + support_file_bytes
            if package_bytes > max_package_bytes:
                risks.append(
                    {
                        "risk": "skill-package-too-large",
                        "path": str(skill_dir),
                        "bytes": package_bytes,
                        "max_bytes": max_package_bytes,
                        "skill_md_bytes": skill_md_bytes,
                        "support_file_bytes": support_file_bytes,
                    }
                )
            skills.append(
                {
                    "category": category,
                    "name": skill_dir.name,
                    "path": str(skill_dir),
                    "import_action": "import-to-db",
                    "storage_boundary": "db",
                    "runtime_artifact_required": False,
                    "deployment_artifact": None,
                    "support_file_count": len(support_files),
                    "support_file_bytes": support_file_bytes,
                    "package_bytes": package_bytes,
                    "risks": risks,
                    "error": error,
                }
            )
    return skills


def collect_runtime_state_inventory(
    *,
    project_root: str | Path,
    state_dir: str | Path,
    skills_root: str | Path | None = None,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    state_dir = Path(state_dir).resolve()
    skills_root = Path(skills_root).resolve() if skills_root is not None else project_root / "skills"

    config = _config_report(project_root)
    channel_runtime = _channel_runtime_report(state_dir)
    extensions = _extensions_report(project_root)
    agents = _agents_report(state_dir)
    user_profiles = _user_profile_reports(state_dir)
    default_agent_souls = _default_agent_soul_reports(state_dir)
    memories = _memory_reports(state_dir)
    skills = _skills_report(skills_root)

    return {
        "config": config,
        "channel_runtime": channel_runtime,
        "extensions": extensions,
        "agents": agents,
        "user_profiles": user_profiles,
        "default_agent_souls": default_agent_souls,
        "memory": memories,
        "skills": skills,
        "summary": {
            "config_files": int(config["exists"]) + int(extensions["exists"]),
            "channel_runtime_configs": int(channel_runtime["exists"]),
            "mcp_servers": len(extensions["mcp_servers"]),
            "agents": len(agents),
            "user_profiles": len(user_profiles),
            "default_agent_souls": len(default_agent_souls),
            "memory_files": len(memories),
            "skills": len(skills),
            "risks": (
                len(config["risks"])
                + len(channel_runtime["risks"])
                + len(extensions["risks"])
                + sum(len(skill.get("risks", [])) for skill in skills)
            ),
        },
    }


def _collect_source_errors(report: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    app_config_error = report["config"].get("error")
    if app_config_error is None and report["config"]["exists"]:
        app_config_error = _app_config_error(report["config"]["path"])
    if app_config_error:
        errors.append(
            {
                "resource": "app_config",
                "code": "invalid_app_config",
                "path": report["config"]["path"],
                "error": app_config_error,
            }
        )
    channel_runtime_error = report["channel_runtime"].get("error")
    if channel_runtime_error:
        errors.append(
            {
                "resource": "channel_runtime_config",
                "code": "invalid_channel_runtime_config",
                "path": report["channel_runtime"]["path"],
                "error": channel_runtime_error,
            }
        )
    extensions_config_error = report["extensions"].get("error")
    if extensions_config_error is None and report["extensions"]["exists"]:
        extensions_config_error = _extensions_config_error(report["extensions"]["path"])
    if extensions_config_error:
        errors.append(
            {
                "resource": "extensions_config",
                "code": "invalid_extensions_config",
                "path": report["extensions"]["path"] or "",
                "error": extensions_config_error,
            }
        )
    for agent in report["agents"]:
        if agent.get("error"):
            errors.append(
                {
                    "resource": "agent",
                    "code": "invalid_agent_config",
                    "owner_user_id": agent["owner_user_id"],
                    "name": agent["name"],
                    "path": agent["path"],
                    "error": agent["error"],
                }
            )
    for memory in report["memory"]:
        if memory.get("error"):
            errors.append(
                {
                    "resource": "memory",
                    "code": "invalid_memory",
                    "path": memory["path"],
                    "error": memory["error"],
                }
            )
    for skill in report["skills"]:
        if skill.get("error"):
            errors.append(
                {
                    "resource": "skill",
                    "code": "invalid_skill_metadata",
                    "category": skill["category"],
                    "name": skill["name"],
                    "path": skill["path"],
                    "error": skill["error"],
                }
            )
        for risk in skill.get("risks", []):
            if risk.get("risk") == "skill-file-too-large":
                errors.append(
                    {
                        "resource": "skill_file",
                        "code": "skill_file_too_large",
                        "category": skill["category"],
                        "name": skill["name"],
                        "path": risk["path"],
                        "error": (
                            "skill support file exceeds DEER_FLOW_DB_SKILL_FILE_MAX_BYTES "
                            f"({risk['bytes']} > {risk['max_bytes']})"
                        ),
                    }
                )
            elif risk.get("risk") == "skill-package-too-large":
                errors.append(
                    {
                        "resource": "skill_package",
                        "code": "skill_package_too_large",
                        "category": skill["category"],
                        "name": skill["name"],
                        "path": risk["path"],
                        "error": (
                            "skill package exceeds DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES "
                            f"({risk['bytes']} > {risk['max_bytes']})"
                        ),
                    }
                )
    return errors


def _database_can_have_conflicts(database_config: DatabaseConfig) -> bool:
    if database_config.backend == "sqlite":
        return Path(database_config.sqlite_path).is_file()
    return True


def _table_exists(inspector, model: type) -> bool:
    return inspector.has_table(model.__tablename__)


def _collect_existing_db_conflicts(report: dict[str, Any], database_config: DatabaseConfig) -> list[dict[str, str]]:
    from deerflow.persistence.agents.model import CustomAgentRow, DefaultAgentSoulRow, UserProfileRow
    from deerflow.persistence.mcp.model import McpServerRow
    from deerflow.persistence.memory.model import MemoryRow
    from deerflow.persistence.runtime_config.model import RuntimeConfigRow
    from deerflow.persistence.skills.model import NormalizedSkillRow, SkillRow

    if not _database_can_have_conflicts(database_config):
        return []

    engine = create_engine(_sync_sqlalchemy_url(database_config))
    inspector = inspect(engine)
    conflicts: list[dict[str, str]] = []
    session_factory = sessionmaker(engine, expire_on_commit=False)

    with session_factory() as session:
        if _table_exists(inspector, RuntimeConfigRow):
            for key, resource in (
                ("app", "app_config"),
                ("extensions", "extensions_config"),
                ("channel_runtime", "channel_runtime_config"),
            ):
                if key == "app" and not report["config"]["exists"]:
                    continue
                if key == "extensions" and not report["extensions"]["exists"]:
                    continue
                if key == "channel_runtime" and not report["channel_runtime"]["exists"]:
                    continue
                if session.get(RuntimeConfigRow, key) is not None:
                    conflicts.append({"resource": resource, "key": key})

        if _table_exists(inspector, McpServerRow):
            for server in report["extensions"]["mcp_servers"]:
                if session.get(McpServerRow, server["name"]) is not None:
                    conflicts.append({"resource": "mcp_server", "name": server["name"]})

        if _table_exists(inspector, UserProfileRow):
            for profile in report["user_profiles"]:
                owner_user_id = profile["owner_user_id"]
                if session.get(UserProfileRow, owner_user_id) is not None:
                    conflicts.append({"resource": "user_profile", "owner_user_id": owner_user_id})

        if _table_exists(inspector, DefaultAgentSoulRow):
            for soul in report["default_agent_souls"]:
                owner_user_id = soul["owner_user_id"]
                if session.get(DefaultAgentSoulRow, owner_user_id) is not None:
                    conflicts.append({"resource": "default_agent_soul", "owner_user_id": owner_user_id})

        if _table_exists(inspector, CustomAgentRow):
            for agent in report["agents"]:
                stmt = select(CustomAgentRow).where(
                    CustomAgentRow.owner_user_id == agent["owner_user_id"],
                    CustomAgentRow.agent_name == agent["name"],
                )
                if session.execute(stmt).scalar_one_or_none() is not None:
                    conflicts.append({"resource": "agent", "owner_user_id": agent["owner_user_id"], "name": agent["name"]})

        if _table_exists(inspector, MemoryRow):
            for memory in report["memory"]:
                stmt = select(MemoryRow).where(
                    MemoryRow.owner_user_id == memory["owner_user_id"],
                    MemoryRow.agent_scope == memory["agent_scope"],
                )
                if session.execute(stmt).scalar_one_or_none() is not None:
                    conflicts.append({"resource": "memory", "owner_user_id": memory["owner_user_id"], "agent_scope": memory["agent_scope"]})

        normalized_skill_conflicts: set[tuple[str, str]] = set()
        if _table_exists(inspector, NormalizedSkillRow):
            for skill in report["skills"]:
                stmt = select(NormalizedSkillRow).where(
                    NormalizedSkillRow.owner_user_id == "",
                    NormalizedSkillRow.category == skill["category"],
                    NormalizedSkillRow.skill_name == skill["name"],
                )
                if session.execute(stmt).scalar_one_or_none() is not None:
                    normalized_skill_conflicts.add((skill["category"], skill["name"]))
                    conflicts.append({"resource": "skill", "category": skill["category"], "name": skill["name"]})

        if _table_exists(inspector, SkillRow):
            for skill in report["skills"]:
                if skill["category"] != "custom":
                    continue
                if (skill["category"], skill["name"]) in normalized_skill_conflicts:
                    continue
                stmt = select(SkillRow).where(
                    SkillRow.owner_user_id == "",
                    SkillRow.skill_name == skill["name"],
                )
                if session.execute(stmt).scalar_one_or_none() is not None:
                    conflicts.append({"resource": "skill", "category": skill["category"], "name": skill["name"]})

    return conflicts


def _preflight_import(report: dict[str, Any], database_config: DatabaseConfig, *, overwrite: bool = False) -> dict[str, Any]:
    errors = _collect_source_errors(report)
    conflicts = _collect_existing_db_conflicts(report, database_config)
    blocking_conflicts = [] if overwrite else conflicts
    return {
        "ok": not errors and not blocking_conflicts,
        "overwrite_enabled": overwrite,
        "errors": errors,
        "conflicts": conflicts,
        "blocking_conflicts": blocking_conflicts,
    }


def _count_conflicts_by_resource(conflicts: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for conflict in conflicts:
        resource = conflict.get("resource")
        if not resource:
            continue
        counts[resource] = counts.get(resource, 0) + 1
    return dict(sorted(counts.items()))


def _sync_sqlalchemy_url(config: DatabaseConfig) -> str:
    if config.backend == "sqlite":
        Path(config.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{config.sqlite_path}"
    if config.backend == "postgres":
        url = config.postgres_url
        if url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    raise ValueError("Runtime state import --apply requires sqlite or postgres database backend")


def _upsert_runtime_config(database_config: DatabaseConfig, key: str, payload: dict[str, Any], *, updated_by: str | None) -> dict[str, Any] | None:
    from deerflow.persistence.base import Base
    from deerflow.persistence.runtime_config.model import RuntimeConfigRow
    from deerflow.persistence.runtime_config.sql import changed_top_level_fields, restart_required_reasons, stable_json_hash

    engine = create_engine(_sync_sqlalchemy_url(database_config))
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    content_hash = stable_json_hash(payload)
    with session_factory() as session:
        row = session.get(RuntimeConfigRow, key)
        previous_payload = dict(row.payload_json) if row is not None else {}
        if row is None:
            row = RuntimeConfigRow(
                key=key,
                payload_json=payload,
                schema_version="1",
                revision=1,
                content_hash=content_hash,
                updated_by=updated_by,
            )
            session.add(row)
        else:
            row.payload_json = payload
            row.schema_version = "1"
            row.revision += 1
            row.content_hash = content_hash
            row.updated_by = updated_by
        session.commit()
    if not previous_payload:
        return None
    changed_fields = changed_top_level_fields(previous_payload, payload)
    reasons = restart_required_reasons(changed_fields)
    if not reasons:
        return None
    return {"fields": list(reasons), "reasons": reasons}


def _load_required_mapping(path: str | None, *, parser: str) -> dict[str, Any] | None:
    if path is None:
        return None
    source = Path(path)
    payload = _read_yaml(source) if parser == "yaml" else _read_json(source)
    if error := payload.get("_error"):
        raise ValueError(f"Cannot import {source}: {error}")
    return payload


def _apply_config_extensions_mcp(
    report: dict[str, Any],
    *,
    database_config: DatabaseConfig,
    updated_by: str | None,
) -> dict[str, Any]:
    from deerflow.config.extensions_config import ExtensionsConfig
    from deerflow.config.extensions_sources import DbExtensionsConfigStore

    applied = {
        "app_config": False,
        "channel_runtime_config": False,
        "extensions_config": False,
        "mcp_servers": 0,
        "user_profiles": 0,
        "default_agent_souls": 0,
        "agents": 0,
        "memory_files": 0,
        "skills": 0,
    }

    app_payload = _load_required_mapping(report["config"]["path"] if report["config"]["exists"] else None, parser="yaml")
    if app_payload is not None:
        restart_report = _upsert_runtime_config(database_config, "app", app_payload, updated_by=updated_by)
        applied["app_config"] = True
        if restart_report is not None:
            applied.setdefault("restart_required", {})["app_config"] = restart_report

    channel_runtime_payload = _load_required_mapping(
        report["channel_runtime"]["path"] if report["channel_runtime"]["exists"] else None,
        parser="json",
    )
    if channel_runtime_payload is not None:
        _upsert_runtime_config(database_config, "channel_runtime", channel_runtime_payload, updated_by=updated_by)
        applied["channel_runtime_config"] = True

    extensions_payload = _load_required_mapping(
        report["extensions"]["path"] if report["extensions"]["exists"] else None,
        parser="json",
    )
    if extensions_payload is not None:
        saved = DbExtensionsConfigStore(database_config=database_config).save_extensions_config(
            ExtensionsConfig.model_validate(extensions_payload),
            updated_by=updated_by,
        )
        applied["extensions_config"] = True
        applied["mcp_servers"] = len(saved.config.mcp_servers)

    return applied


def _apply_agents_and_profiles(
    report: dict[str, Any],
    *,
    database_config: DatabaseConfig,
) -> dict[str, int]:
    from deerflow.config.agent_store import DbAgentStore
    from deerflow.config.agents_config import AgentConfig

    store = DbAgentStore(database_config=database_config)
    user_profiles = 0
    for profile in report["user_profiles"]:
        content = Path(profile["path"]).read_text(encoding="utf-8")
        store.save_user_profile(profile["owner_user_id"], content)
        user_profiles += 1

    default_agent_souls = 0
    for soul in report["default_agent_souls"]:
        content = Path(soul["path"]).read_text(encoding="utf-8")
        store.save_default_agent_soul(soul["owner_user_id"], content)
        default_agent_souls += 1

    agents = 0
    for agent in report["agents"]:
        agent_dir = Path(agent["path"])
        config_payload = _read_yaml(agent_dir / "config.yaml")
        if error := config_payload.get("_error"):
            raise ValueError(f"Cannot import agent {agent['name']} from {agent_dir}: {error}")
        config_payload.setdefault("name", agent["name"])
        soul_path = agent_dir / "SOUL.md"
        soul = soul_path.read_text(encoding="utf-8") if soul_path.is_file() else ""
        store.save_agent(
            agent["owner_user_id"],
            agent["name"],
            AgentConfig.model_validate(config_payload),
            soul,
        )
        agents += 1

    return {
        "user_profiles": user_profiles,
        "default_agent_souls": default_agent_souls,
        "agents": agents,
    }


def _apply_memory(
    report: dict[str, Any],
    *,
    database_config: DatabaseConfig,
) -> dict[str, int]:
    from deerflow.agents.memory.storage import DbMemoryStorage

    storage = DbMemoryStorage(database_config=database_config)
    memory_files = 0
    for memory in report["memory"]:
        payload = _read_json(Path(memory["path"]))
        if error := payload.get("_error"):
            raise ValueError(f"Cannot import memory {memory['path']}: {error}")
        agent_scope = memory["agent_scope"] or None
        if not storage.save(payload, agent_scope, user_id=memory["owner_user_id"]):
            raise RuntimeError(f"Failed to import memory {memory['path']}")
        memory_files += 1
    return {"memory_files": memory_files}


def _apply_skills(
    report: dict[str, Any],
    *,
    database_config: DatabaseConfig,
) -> dict[str, int]:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    imported = 0
    imported_public = 0
    imported_custom = 0
    with tempfile.TemporaryDirectory() as tmp:
        storage = DbSkillStorage(host_path=str(Path(tmp) / "skills"), database_config=database_config)
        for skill in report["skills"]:
            if skill["category"] == "public":
                storage.seed_public_skill_from_directory(skill["path"])
                imported += 1
                imported_public += 1
                continue
            skill_dir = Path(skill["path"])
            skill_md = skill_dir / "SKILL.md"
            storage.write_custom_skill(skill["name"], "SKILL.md", skill_md.read_text(encoding="utf-8"))
            for child in sorted(skill_dir.rglob("*")):
                if not child.is_file() or child.name == "SKILL.md":
                    continue
                relative_path = child.relative_to(skill_dir).as_posix()
                raw_content = child.read_bytes()
                try:
                    support_content: str | bytes = raw_content.decode("utf-8")
                except UnicodeDecodeError:
                    support_content = raw_content
                storage._write_custom_skill_payload(
                    skill["name"],
                    relative_path,
                    support_content,
                    mode=stat.S_IMODE(child.stat().st_mode),
                )
            imported += 1
            imported_custom += 1
    return {
        "skills": imported,
        "public_skills": imported_public,
        "custom_skills": imported_custom,
        "skipped_public_skills": 0,
    }


def import_runtime_state_to_db(
    *,
    project_root: str | Path,
    state_dir: str | Path,
    skills_root: str | Path | None,
    database_config: DatabaseConfig,
    apply: bool = False,
    overwrite: bool = False,
    updated_by: str | None = None,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    resolved_skills_root = Path(skills_root).resolve() if skills_root is not None else project_root / "skills"
    report = collect_runtime_state_inventory(
        project_root=project_root,
        state_dir=state_dir,
        skills_root=resolved_skills_root,
    )
    report["mode"] = "apply" if apply else "dry-run"
    report["database"] = {
        "backend": database_config.backend,
        "sqlite_path": database_config.sqlite_path if database_config.backend == "sqlite" else None,
    }
    report["preflight"] = _preflight_import(report, database_config, overwrite=overwrite)
    report["summary"]["preflight_errors"] = len(report["preflight"]["errors"])
    report["summary"]["conflicts"] = len(report["preflight"]["conflicts"])
    report["summary"]["blocking_conflicts"] = len(report["preflight"]["blocking_conflicts"])
    if apply:
        if not report["preflight"]["ok"]:
            raise RuntimeError(f"Preflight failed: {json.dumps(report['preflight'], ensure_ascii=False, sort_keys=True)}")
        report["applied"] = _apply_config_extensions_mcp(
            report,
            database_config=database_config,
            updated_by=updated_by,
        )
        report["applied"].update(
            _apply_agents_and_profiles(
                report,
                database_config=database_config,
            )
        )
        report["applied"].update(
            _apply_memory(
                report,
                database_config=database_config,
            )
        )
        report["applied"].update(
            _apply_skills(
                report,
                database_config=database_config,
            )
        )
        if overwrite:
            conflicts = report["preflight"]["conflicts"]
            report["applied"]["overwritten_conflicts"] = len(conflicts)
            report["applied"]["overwritten_by_resource"] = _count_conflicts_by_resource(conflicts)
    return report


def _run_cli() -> None:
    from deerflow.config.bootstrap import database_config_from_url, get_bootstrap_database_config
    from deerflow.config.database_config import DatabaseConfig
    from deerflow.config.runtime_paths import runtime_home

    parser = argparse.ArgumentParser(description="Plan import of Harness runtime state into DB-backed stores")
    parser.add_argument("--project-root", default=".", help="Project root containing config.yaml and extensions_config.json")
    parser.add_argument("--state-dir", default=None, help="State directory containing memory, agents, and users; defaults to DEER_FLOW_HOME/.deer-flow resolution")
    parser.add_argument("--skills-root", default=None, help="Skills root containing public/ and custom/")
    parser.add_argument("--apply", action="store_true", help="Apply config, channel runtime config, extensions, MCP, agents, memory, and custom skills after preflight passes.")
    parser.add_argument("--overwrite", action="store_true", help="Allow --apply to replace existing DB rows reported as preflight conflicts.")
    parser.add_argument("--database-url", default=None, help="Optional sqlite/postgresql SQLAlchemy URL for this migration run; overrides DEER_FLOW_DATABASE_URL.")
    parser.add_argument("--updated-by", default="runtime-state-import", help="Audit value for DB updated_by fields when --apply is used")
    args = parser.parse_args()

    state_dir = Path(args.state_dir).resolve() if args.state_dir else runtime_home()
    try:
        database_config = database_config_from_url(args.database_url) if args.database_url else get_bootstrap_database_config() or DatabaseConfig()
    except ValueError as exc:
        parser.error(str(exc))
    report = import_runtime_state_to_db(
        project_root=Path(args.project_root).resolve(),
        state_dir=state_dir,
        skills_root=Path(args.skills_root).resolve() if args.skills_root else None,
        database_config=database_config,
        apply=args.apply,
        overwrite=args.overwrite,
        updated_by=args.updated_by,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


def main() -> None:
    with _suppress_cli_runtime_noise():
        _run_cli()


if __name__ == "__main__":
    main()
