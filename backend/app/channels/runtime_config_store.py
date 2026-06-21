"""Local persistence for runtime IM channel configuration."""

from __future__ import annotations

import copy
import json
import logging
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.base import Base
from deerflow.persistence.runtime_config.model import RuntimeConfigRow
from deerflow.persistence.runtime_config.sql import stable_json_hash

logger = logging.getLogger(__name__)

RUNTIME_CHANNEL_DISABLED_FLAG = "_runtime_disabled"
_CHANNEL_RUNTIME_CONFIG_KEY = "channel_runtime"


@runtime_checkable
class ChannelRuntimeConfigStoreProtocol(Protocol):
    def load_all(self) -> dict[str, dict[str, Any]]: ...

    def get_provider_config(self, provider: str) -> dict[str, Any] | None: ...

    def set_provider_config(self, provider: str, config: dict[str, Any]) -> None: ...

    def set_provider_disconnected(self, provider: str) -> None: ...

    def remove_provider_config(self, provider: str) -> bool: ...


class ChannelRuntimeConfigStore:
    """JSON-backed store for channel credentials entered from the UI.

    This intentionally mirrors ``ChannelStore``: local/private deployments get
    durable runtime configuration without needing a public callback URL or a
    config.yaml edit.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            from deerflow.config.paths import get_paths

            path = Path(get_paths().base_dir) / "channels" / "runtime-config.json"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt channel runtime config store at %s, starting fresh", self._path)
                return {}
            if isinstance(raw, dict):
                return {str(name): dict(value) for name, value in raw.items() if isinstance(value, dict)}
        return {}

    def _save(self) -> None:
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=self._path.parent,
            suffix=".tmp",
            delete=False,
        )
        try:
            try:
                Path(fd.name).chmod(0o600)
            except OSError:
                logger.debug("Unable to chmod temporary channel runtime config store at %s", fd.name, exc_info=True)
            json.dump(self._data, fd, indent=2, ensure_ascii=False)
            fd.close()
            Path(fd.name).replace(self._path)
            try:
                self._path.chmod(0o600)
            except OSError:
                logger.debug("Unable to chmod channel runtime config store at %s", self._path, exc_info=True)
        except BaseException:
            fd.close()
            Path(fd.name).unlink(missing_ok=True)
            raise

    def load_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: dict(config) for name, config in self._data.items()}

    def get_provider_config(self, provider: str) -> dict[str, Any] | None:
        with self._lock:
            config = self._data.get(provider)
            return dict(config) if isinstance(config, dict) else None

    def set_provider_config(self, provider: str, config: dict[str, Any]) -> None:
        with self._lock:
            self._data[provider] = dict(config)
            self._save()

    def set_provider_disconnected(self, provider: str) -> None:
        with self._lock:
            self._data[provider] = {
                "enabled": False,
                RUNTIME_CHANNEL_DISABLED_FLAG: True,
            }
            self._save()

    def remove_provider_config(self, provider: str) -> bool:
        with self._lock:
            if provider not in self._data:
                return False
            del self._data[provider]
            self._save()
            return True


class DbChannelRuntimeConfigStore:
    """DB-backed store for channel credentials entered from the UI."""

    def __init__(
        self,
        *,
        database_config: DatabaseConfig | None = None,
        key: str = _CHANNEL_RUNTIME_CONFIG_KEY,
    ) -> None:
        self._database_config = database_config or _resolve_database_config()
        self._key = key
        self._engine = create_engine(self._sync_sqlalchemy_url(self._database_config))
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)
        self._lock = threading.Lock()

    @staticmethod
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
        raise ValueError("DbChannelRuntimeConfigStore requires sqlite or postgres database backend")

    @staticmethod
    def _normalize_payload(payload: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            return {}
        return {str(name): dict(config) for name, config in payload.items() if isinstance(config, dict)}

    @classmethod
    def _load_payload(cls, session: Session, key: str) -> dict[str, dict[str, Any]]:
        row = session.get(RuntimeConfigRow, key)
        if row is None:
            return {}
        return cls._normalize_payload(row.payload_json)

    @staticmethod
    def _save_payload(session: Session, key: str, payload: dict[str, dict[str, Any]]) -> None:
        payload_copy = copy.deepcopy(payload)
        content_hash = stable_json_hash(payload_copy)
        row = session.get(RuntimeConfigRow, key)
        if row is None:
            session.add(
                RuntimeConfigRow(
                    key=key,
                    payload_json=payload_copy,
                    schema_version="1",
                    revision=1,
                    content_hash=content_hash,
                    updated_by="channel-runtime",
                )
            )
            return
        row.payload_json = payload_copy
        row.schema_version = "1"
        row.revision += 1
        row.content_hash = content_hash
        row.updated_by = "channel-runtime"

    def load_all(self) -> dict[str, dict[str, Any]]:
        with self._session_factory() as session:
            return copy.deepcopy(self._load_payload(session, self._key))

    def get_provider_config(self, provider: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            config = self._load_payload(session, self._key).get(provider)
            return copy.deepcopy(config) if isinstance(config, dict) else None

    def set_provider_config(self, provider: str, config: dict[str, Any]) -> None:
        with self._lock, self._session_factory() as session:
            payload = self._load_payload(session, self._key)
            payload[provider] = dict(config)
            self._save_payload(session, self._key, payload)
            session.commit()

    def set_provider_disconnected(self, provider: str) -> None:
        with self._lock, self._session_factory() as session:
            payload = self._load_payload(session, self._key)
            payload[provider] = {
                "enabled": False,
                RUNTIME_CHANNEL_DISABLED_FLAG: True,
            }
            self._save_payload(session, self._key, payload)
            session.commit()

    def remove_provider_config(self, provider: str) -> bool:
        with self._lock, self._session_factory() as session:
            payload = self._load_payload(session, self._key)
            if provider not in payload:
                return False
            del payload[provider]
            self._save_payload(session, self._key, payload)
            session.commit()
            return True


def _resolve_database_config() -> DatabaseConfig:
    from deerflow.config.app_config import get_app_config
    from deerflow.config.bootstrap import get_bootstrap_database_config, is_db_config_enabled

    if is_db_config_enabled():
        bootstrap_database_config = get_bootstrap_database_config()
        if bootstrap_database_config is not None:
            return bootstrap_database_config
    return get_app_config().database


def get_channel_runtime_config_store(
    path: str | Path | None = None,
    *,
    database_config: DatabaseConfig | None = None,
) -> ChannelRuntimeConfigStoreProtocol:
    from deerflow.config.bootstrap import is_db_config_enabled

    if database_config is not None or is_db_config_enabled():
        return DbChannelRuntimeConfigStore(database_config=database_config)
    return ChannelRuntimeConfigStore(path)


def _provider_enabled(channel_connections_config: Any, provider: str) -> bool:
    provider_config = getattr(channel_connections_config, provider, None)
    return bool(getattr(provider_config, "enabled", False))


def _runtime_channel_disconnected(runtime_config: dict[str, Any]) -> bool:
    return runtime_config.get(RUNTIME_CHANNEL_DISABLED_FLAG) is True and runtime_config.get("enabled") is False


def merge_runtime_channel_configs(
    channels_config: dict[str, Any],
    channel_connections_config: Any,
    *,
    store: ChannelRuntimeConfigStoreProtocol | None = None,
) -> None:
    """Merge persisted runtime provider config into ``channels_config`` in-place."""
    if channel_connections_config is None or not getattr(channel_connections_config, "enabled", False):
        return

    runtime_store = store or get_channel_runtime_config_store()
    for provider, runtime_config in runtime_store.load_all().items():
        if not _provider_enabled(channel_connections_config, provider):
            continue
        if _runtime_channel_disconnected(runtime_config):
            channels_config.pop(provider, None)
            continue
        existing = channels_config.get(provider)
        merged = dict(runtime_config)
        if isinstance(existing, dict):
            merged.update(existing)
        channels_config[provider] = merged


def apply_runtime_connection_config(
    channel_connections_config: Any,
    *,
    store: ChannelRuntimeConfigStoreProtocol | None = None,
) -> Any:
    """Apply persisted connection metadata that lives outside ``channels``.

    Telegram uses a bot username for deep links; UI-entered values are stored
    with the runtime channel config so local restarts keep the provider
    configured.
    """
    if channel_connections_config is None or not getattr(channel_connections_config, "enabled", False):
        return channel_connections_config

    runtime_store = store or get_channel_runtime_config_store()
    telegram_runtime_config = runtime_store.get_provider_config("telegram")
    bot_username = ""
    if isinstance(telegram_runtime_config, dict):
        bot_username = str(telegram_runtime_config.get("bot_username") or "").strip()
    if not bot_username or not _provider_enabled(channel_connections_config, "telegram"):
        return channel_connections_config

    config = channel_connections_config.model_copy(deep=True)
    config.telegram.bot_username = bot_username
    return config
