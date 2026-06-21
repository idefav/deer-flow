import pytest
from pydantic import ValidationError

from deerflow.config.app_config import AppConfig


def _minimal_app_config_payload(**sections: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
    }
    payload.update(sections)
    return payload


def test_runtime_storage_defaults_to_filesystem_mode():
    config = AppConfig.model_validate(_minimal_app_config_payload())

    assert config.runtime_storage.backend == "filesystem"
    assert config.runtime_storage.object_store is None


def test_runtime_storage_object_mode_requires_object_store_config():
    with pytest.raises(ValidationError, match="object_store"):
        AppConfig.model_validate(
            _minimal_app_config_payload(
                runtime_storage={
                    "backend": "object",
                }
            )
        )


def test_runtime_storage_object_mode_parses_s3_defaults():
    config = AppConfig.model_validate(
        _minimal_app_config_payload(
            runtime_storage={
                "backend": "object",
                "object_store": {
                    "endpoint_url": "http://seaweedfs-s3:8333",
                    "bucket": "deerflow-runtime",
                },
            }
        )
    )

    object_store = config.runtime_storage.object_store
    assert object_store is not None
    assert object_store.provider == "s3"
    assert object_store.endpoint_url == "http://seaweedfs-s3:8333"
    assert object_store.bucket == "deerflow-runtime"
    assert object_store.region == "us-east-1"
    assert object_store.prefix == "deerflow"
    assert object_store.path_style is True
    assert object_store.max_materialize_files == 10000
    assert object_store.max_materialize_bytes == 536870912
    assert object_store.max_single_object_bytes == 104857600
