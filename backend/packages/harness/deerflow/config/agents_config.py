"""Configuration and loaders for custom agents.

Custom agents are stored per-user under ``{base_dir}/users/{user_id}/agents/{name}/``.
A legacy shared layout at ``{base_dir}/agents/{name}/`` is still readable so that
installations that pre-date user isolation continue to work until they run the
``scripts/migrate_user_isolation.py`` migration. New writes always target the
per-user layout.
"""

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from deerflow.config.bootstrap import is_db_config_enabled
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


def validate_agent_name(name: str | None) -> str | None:
    """Validate a custom agent name before using it in filesystem paths."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Invalid agent name. Expected a string or None.")
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    return name


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None
    # skills controls which skills are loaded into the agent's prompt:
    # - None (or omitted): load all enabled skills (default fallback behavior)
    # - [] (explicit empty list): disable all skills
    # - ["skill1", "skill2"]: load only the specified skills
    skills: list[str] | None = None


class DefaultAgentSoulConflictError(RuntimeError):
    """Raised when default-agent SOUL content changed before a conditional save."""


@dataclass(frozen=True)
class DefaultAgentSoulState:
    content: str | None
    revision: int


def _load_db_agent_store():
    from deerflow.config.agent_store import DbAgentStore

    return DbAgentStore()


def _effective_user_id(user_id: str | None) -> str:
    return user_id or get_effective_user_id()


def resolve_agent_dir(name: str, *, user_id: str | None = None) -> Path:
    """Return the on-disk directory for an agent, preferring the per-user layout.

    Resolution order:
    1. ``{base_dir}/users/{user_id}/agents/{name}/`` (per-user, current layout).
    2. ``{base_dir}/agents/{name}/`` (legacy shared layout — read-only fallback).

    If neither exists, the per-user path is returned so callers that intend to
    create the agent write into the new layout.

    Args:
        name: Validated agent name.
        user_id: Owner of the agent. Defaults to the effective user from the
            request context (or ``"default"`` in no-auth mode).
    """
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()
    user_path = paths.user_agent_dir(effective_user, name)
    # Require config.yaml to confirm this is a genuine agent directory,
    # not a leftover from memory/storage writes (see #3390).
    if user_path.exists() and (user_path / "config.yaml").exists():
        return user_path

    legacy_path = paths.agent_dir(name)
    if legacy_path.exists() and (legacy_path / "config.yaml").exists():
        return legacy_path

    return user_path


def _load_agent_config_from_file(name: str | None, *, user_id: str | None = None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory.

    Reads from the per-user layout first; falls back to the legacy shared layout
    for installations that have not yet been migrated.

    Args:
        name: The agent name.
        user_id: Owner of the agent. Defaults to the effective user from the
            current request context.

    Returns:
        AgentConfig instance, or ``None`` if ``name`` is ``None``.

    Raises:
        FileNotFoundError: If the agent directory or config.yaml does not exist.
        ValueError: If config.yaml cannot be parsed.
    """

    if name is None:
        return None

    name = validate_agent_name(name)
    agent_dir = resolve_agent_dir(name, user_id=user_id)
    config_file = agent_dir / "config.yaml"

    if not agent_dir.exists():
        raise FileNotFoundError(f"Agent directory not found: {agent_dir}")

    if not config_file.exists():
        raise FileNotFoundError(f"Agent config not found: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse agent config {config_file}: {e}") from e

    # Ensure name is set from directory name if not in file
    if "name" not in data:
        data["name"] = name

    # Strip unknown fields before passing to Pydantic (e.g. legacy prompt_file)
    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}

    return AgentConfig(**data)


def load_agent_config(name: str | None, *, user_id: str | None = None) -> AgentConfig | None:
    """Load the custom or default agent's config from the active store."""
    if name is None:
        return None
    name = validate_agent_name(name)
    if is_db_config_enabled():
        effective_user = _effective_user_id(user_id)
        config = _load_db_agent_store().load_agent_config(effective_user, name)
        if config is None:
            raise FileNotFoundError(f"Agent '{name}' not found in DB store for user {effective_user!r}")
        return config
    return _load_agent_config_from_file(name, user_id=user_id)


def agent_config_exists(name: str, *, user_id: str | None = None) -> bool:
    """Return whether an agent exists in the active custom-agent store."""
    name = validate_agent_name(name)
    if is_db_config_enabled():
        return _load_db_agent_store().load_agent_config(_effective_user_id(user_id), name) is not None
    try:
        _load_agent_config_from_file(name, user_id=user_id)
    except FileNotFoundError:
        return False
    return True


def save_agent_config(name: str, config: AgentConfig | dict, soul: str, *, user_id: str | None = None) -> AgentConfig:
    """Save a custom agent into the active custom-agent store."""
    name = validate_agent_name(name)
    agent_config = config if isinstance(config, AgentConfig) else AgentConfig.model_validate(config)
    agent_config = agent_config.model_copy(update={"name": name})

    if is_db_config_enabled():
        return _load_db_agent_store().save_agent(_effective_user_id(user_id), name, agent_config, soul)

    agent_dir = get_paths().user_agent_dir(_effective_user_id(user_id), name)
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_data = agent_config.model_dump(exclude_none=True)
    config_data["name"] = name
    with open(agent_dir / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    (agent_dir / SOUL_FILENAME).write_text(soul, encoding="utf-8")
    return agent_config


def delete_agent_config(name: str, *, user_id: str | None = None) -> bool:
    """Delete a custom agent from the active custom-agent store."""
    name = validate_agent_name(name)
    if is_db_config_enabled():
        return _load_db_agent_store().delete_agent(_effective_user_id(user_id), name)

    agent_dir = get_paths().user_agent_dir(_effective_user_id(user_id), name)
    if not agent_dir.exists():
        return False
    shutil.rmtree(agent_dir)
    return True


def _load_agent_soul_from_file(agent_name: str | None, *, user_id: str | None = None) -> str | None:
    """Read the SOUL.md file for a custom agent, if it exists.

    SOUL.md defines the agent's personality, values, and behavioral guardrails.
    It is injected into the lead agent's system prompt as additional context.

    Args:
        agent_name: The name of the agent or None for the default agent.
        user_id: Owner of the agent. Defaults to the effective user from the
            current request context.

    Returns:
        The SOUL.md content as a string, or None if the file does not exist.
    """
    if agent_name:
        agent_dir = resolve_agent_dir(agent_name, user_id=user_id)
    else:
        agent_dir = get_paths().base_dir
    soul_path = agent_dir / SOUL_FILENAME
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def load_agent_soul(agent_name: str | None, *, user_id: str | None = None) -> str | None:
    """Read SOUL content from the active agent store."""
    if agent_name is not None:
        agent_name = validate_agent_name(agent_name)
    if is_db_config_enabled():
        effective_user = _effective_user_id(user_id)
        if agent_name is None:
            return _load_db_agent_store().load_default_agent_soul(effective_user)
        return _load_db_agent_store().load_agent_soul(effective_user, agent_name)
    return _load_agent_soul_from_file(agent_name, user_id=user_id)


def load_default_agent_soul_state(*, user_id: str | None = None) -> DefaultAgentSoulState:
    """Load default-agent SOUL content and revision from the active store."""
    if is_db_config_enabled():
        content, revision = _load_db_agent_store().load_default_agent_soul_state(_effective_user_id(user_id))
        return DefaultAgentSoulState(content=content, revision=revision)

    paths = get_paths()
    soul_path = paths.base_dir / SOUL_FILENAME
    if not soul_path.exists():
        return DefaultAgentSoulState(content=None, revision=0)
    content = _load_agent_soul_from_file(None, user_id=user_id)
    return DefaultAgentSoulState(content=content, revision=soul_path.stat().st_mtime_ns)


def save_default_agent_soul_state(
    soul: str,
    *,
    user_id: str | None = None,
    expected_revision: int | None = None,
) -> DefaultAgentSoulState:
    """Save default-agent SOUL content and return the updated revision."""
    if is_db_config_enabled():
        from deerflow.config.agent_store import DefaultAgentSoulStoreConflictError

        store = _load_db_agent_store()
        try:
            store.save_default_agent_soul(_effective_user_id(user_id), soul, expected_revision=expected_revision)
        except DefaultAgentSoulStoreConflictError as exc:
            raise DefaultAgentSoulConflictError(str(exc)) from exc
        return load_default_agent_soul_state(user_id=user_id)

    current = load_default_agent_soul_state(user_id=user_id)
    if expected_revision is not None and current.revision != expected_revision:
        raise DefaultAgentSoulConflictError(
            f"Default-agent SOUL revision conflict: expected {expected_revision}, found {current.revision}"
        )

    paths = get_paths()
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    (paths.base_dir / SOUL_FILENAME).write_text(soul, encoding="utf-8")
    return load_default_agent_soul_state(user_id=user_id)


def save_default_agent_soul(soul: str, *, user_id: str | None = None, expected_revision: int | None = None) -> str | None:
    """Save default-agent SOUL content into the active store."""
    return save_default_agent_soul_state(soul, user_id=user_id, expected_revision=expected_revision).content


def _list_custom_agents_from_file(*, user_id: str | None = None) -> list[AgentConfig]:
    """Scan the agents directory and return all valid custom agents.

    Returns the union of agents in the per-user layout and the legacy shared
    layout, so that pre-migration installations remain visible until they are
    migrated. Per-user entries shadow legacy entries with the same name.

    Args:
        user_id: Owner whose agents to list. Defaults to the effective user
            from the current request context.

    Returns:
        List of AgentConfig for each valid agent directory found.
    """
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()

    seen: set[str] = set()
    agents: list[AgentConfig] = []

    user_root = paths.user_agents_dir(effective_user)
    legacy_root = paths.agents_dir

    for root in (user_root, legacy_root):
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in seen:
                continue
            config_file = entry / "config.yaml"
            if not config_file.exists():
                logger.debug(f"Skipping {entry.name}: no config.yaml")
                continue

            try:
                agent_cfg = _load_agent_config_from_file(entry.name, user_id=effective_user)
                if agent_cfg is None:
                    continue
                agents.append(agent_cfg)
                seen.add(entry.name)
            except Exception as e:
                logger.warning(f"Skipping agent '{entry.name}': {e}")

    agents.sort(key=lambda a: a.name)
    return agents


def list_custom_agents(*, user_id: str | None = None) -> list[AgentConfig]:
    """List custom agents from the active store."""
    if is_db_config_enabled():
        effective_user = _effective_user_id(user_id)
        return _load_db_agent_store().list_agents(effective_user)
    return _list_custom_agents_from_file(user_id=user_id)


def load_user_profile(*, user_id: str | None = None) -> str | None:
    """Load USER.md-equivalent profile content from the active store."""
    if is_db_config_enabled():
        content = _load_db_agent_store().load_user_profile(_effective_user_id(user_id))
    else:
        profile_path = get_paths().user_md_file
        if not profile_path.exists():
            return None
        content = profile_path.read_text(encoding="utf-8")
    content = content.strip() if content is not None else None
    return content or None


def save_user_profile(content: str, *, user_id: str | None = None) -> str | None:
    """Save USER.md-equivalent profile content into the active store."""
    if is_db_config_enabled():
        saved = _load_db_agent_store().save_user_profile(_effective_user_id(user_id), content)
        return saved or None

    paths = get_paths()
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    paths.user_md_file.write_text(content, encoding="utf-8")
    return content or None
