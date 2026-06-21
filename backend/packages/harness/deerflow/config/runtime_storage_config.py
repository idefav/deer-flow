from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

RuntimeStorageBackend = Literal["filesystem", "object"]
ObjectStoreProvider = Literal["s3"]


class ObjectStoreConfig(BaseModel):
    """S3-compatible object store settings for stateless runtime artifacts."""

    provider: ObjectStoreProvider = Field(default="s3", description="Object store protocol provider. Currently S3-compatible APIs are supported.")
    endpoint_url: str | None = Field(default=None, description="S3-compatible endpoint URL, for example SeaweedFS S3.")
    bucket: str = Field(description="Bucket used for runtime workspaces, uploads, outputs, and ACP workspace files.")
    region: str = Field(default="us-east-1", description="S3 region name used by the client.")
    prefix: str = Field(default="deerflow", description="Object key prefix under the bucket.")
    access_key_env: str = Field(default="DEER_FLOW_OBJECT_STORE_ACCESS_KEY", description="Environment variable containing the access key.")
    secret_key_env: str = Field(default="DEER_FLOW_OBJECT_STORE_SECRET_KEY", description="Environment variable containing the secret key.")
    path_style: bool = Field(default=True, description="Use path-style addressing for S3-compatible stores.")
    tls_verify: bool = Field(default=True, description="Verify TLS certificates when using HTTPS endpoints.")
    max_materialize_files: int = Field(default=10000, ge=1, description="Maximum files to materialize into a sandbox before a run.")
    max_materialize_bytes: int = Field(default=536870912, ge=1, description="Maximum total bytes to materialize into a sandbox before a run.")
    max_single_object_bytes: int = Field(default=104857600, ge=1, description="Maximum size accepted for one runtime object.")

    @field_validator("bucket")
    @classmethod
    def _validate_bucket(cls, value: str) -> str:
        bucket = value.strip()
        if not bucket:
            raise ValueError("bucket must not be empty")
        return bucket

    @field_validator("prefix")
    @classmethod
    def _normalize_prefix(cls, value: str) -> str:
        return value.strip().strip("/")


class RuntimeStorageConfig(BaseModel):
    """Runtime artifact storage mode."""

    backend: RuntimeStorageBackend = Field(default="filesystem", description="Runtime artifact storage backend.")
    object_store: ObjectStoreConfig | None = Field(default=None, description="S3-compatible object store settings when backend is object.")

    @model_validator(mode="after")
    def _validate_object_store(self) -> Self:
        if self.backend == "object" and self.object_store is None:
            raise ValueError("runtime_storage.object_store is required when runtime_storage.backend is object")
        return self
