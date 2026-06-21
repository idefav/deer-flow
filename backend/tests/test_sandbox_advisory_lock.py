import pytest

from deerflow.artifacts.sandbox_lock import make_sandbox_creation_lock, sandbox_advisory_lock_key
from deerflow.config.app_config import AppConfig


def _app_config(database_backend: str = "postgres") -> AppConfig:
    database = {"backend": database_backend}
    if database_backend == "postgres":
        database["postgres_url"] = "postgresql://user:pass@db/deerflow"
    return AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
            "database": database,
            "runtime_storage": {
                "backend": "object",
                "object_store": {"bucket": "deerflow-runtime"},
            },
        }
    )


def test_sandbox_advisory_lock_key_is_stable_63_bit_integer():
    first = sandbox_advisory_lock_key("thread-1", "sandbox-1")
    second = sandbox_advisory_lock_key("thread-1", "sandbox-1")

    assert first == second
    assert 0 <= first < (1 << 63)


def test_object_runtime_sandbox_lock_requires_postgres_database():
    with pytest.raises(RuntimeError, match="database.backend=postgres"):
        make_sandbox_creation_lock(_app_config("sqlite"), "thread-1", "sandbox-1")


def test_object_runtime_sandbox_lock_uses_configured_postgres_url():
    lock = make_sandbox_creation_lock(_app_config("postgres"), "thread-1", "sandbox-1")

    assert lock.postgres_url == "postgresql://user:pass@db/deerflow"
    assert lock.lock_key == sandbox_advisory_lock_key("thread-1", "sandbox-1")
