from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from deerflow.config.runtime_storage_config import ObjectStoreConfig, RuntimeStorageConfig

USER_DATA_ROOT = "/mnt/user-data"
ACP_WORKSPACE_ROOT = "/mnt/acp-workspace"
USER_DATA_SCOPES = frozenset({"workspace", "uploads", "outputs"})
DEERFLOW_SHA256_METADATA = "deerflow-sha256"


class ArtifactPathError(ValueError):
    """Raised when a runtime artifact path escapes the sandbox file contract."""


@dataclass(frozen=True)
class ParsedArtifactPath:
    virtual_path: str
    storage_relative_path: str
    scope: str


@dataclass(frozen=True)
class ArtifactMetadata:
    object_key: str
    virtual_path: str
    size: int
    sha256: str
    content_type: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ArtifactStore(Protocol):
    """Storage contract for thread runtime artifacts."""

    def put_bytes(
        self,
        user_id: str,
        thread_id: str,
        virtual_path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ArtifactMetadata:
        """Write bytes at a sandbox-visible virtual path."""

    def get_bytes(self, user_id: str, thread_id: str, virtual_path: str) -> bytes:
        """Read bytes from a sandbox-visible virtual path."""

    def list_files(self, user_id: str, thread_id: str, virtual_prefix: str) -> list[ArtifactMetadata]:
        """List files below a sandbox-visible virtual path prefix."""

    def delete(self, user_id: str, thread_id: str, virtual_path: str) -> None:
        """Delete a sandbox-visible runtime artifact if it exists."""


@dataclass(frozen=True)
class _StoredArtifact:
    data: bytes
    metadata: ArtifactMetadata


def artifact_object_key(*, prefix: str, user_id: str, thread_id: str, virtual_path: str) -> str:
    parsed = parse_artifact_virtual_path(virtual_path)
    return _object_key(prefix=prefix, user_id=user_id, thread_id=thread_id, storage_relative_path=parsed.storage_relative_path)


def artifact_object_prefix(*, prefix: str, user_id: str, thread_id: str, virtual_prefix: str) -> str:
    parsed = parse_artifact_virtual_path(virtual_prefix, allow_root=True)
    return _object_key(prefix=prefix, user_id=user_id, thread_id=thread_id, storage_relative_path=parsed.storage_relative_path)


def parse_artifact_virtual_path(virtual_path: str, *, allow_root: bool = False) -> ParsedArtifactPath:
    if not virtual_path.startswith("/"):
        raise ArtifactPathError(f"runtime artifact path must be absolute: {virtual_path}")

    if virtual_path == USER_DATA_ROOT or virtual_path.startswith(f"{USER_DATA_ROOT}/"):
        return _parse_user_data_path(virtual_path, allow_root=allow_root)
    if virtual_path == ACP_WORKSPACE_ROOT or virtual_path.startswith(f"{ACP_WORKSPACE_ROOT}/"):
        return _parse_acp_workspace_path(virtual_path, allow_root=allow_root)
    raise ArtifactPathError(f"runtime artifact path must be under {USER_DATA_ROOT} or {ACP_WORKSPACE_ROOT}: {virtual_path}")


class InMemoryArtifactStore:
    """In-memory ArtifactStore implementation used by focused unit tests."""

    def __init__(self, *, prefix: str = "deerflow") -> None:
        self._prefix = prefix
        self._objects: dict[str, _StoredArtifact] = {}

    def put_bytes(
        self,
        user_id: str,
        thread_id: str,
        virtual_path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ArtifactMetadata:
        object_key = artifact_object_key(prefix=self._prefix, user_id=user_id, thread_id=thread_id, virtual_path=virtual_path)
        parsed = parse_artifact_virtual_path(virtual_path)
        payload = bytes(data)
        item = ArtifactMetadata(
            object_key=object_key,
            virtual_path=parsed.virtual_path,
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
        self._objects[object_key] = _StoredArtifact(data=payload, metadata=item)
        return item

    def get_bytes(self, user_id: str, thread_id: str, virtual_path: str) -> bytes:
        object_key = artifact_object_key(prefix=self._prefix, user_id=user_id, thread_id=thread_id, virtual_path=virtual_path)
        try:
            return self._objects[object_key].data
        except KeyError as exc:
            raise FileNotFoundError(virtual_path) from exc

    def list_files(self, user_id: str, thread_id: str, virtual_prefix: str) -> list[ArtifactMetadata]:
        object_prefix = artifact_object_prefix(prefix=self._prefix, user_id=user_id, thread_id=thread_id, virtual_prefix=virtual_prefix)
        if object_prefix:
            object_prefix = f"{object_prefix.rstrip('/')}/"
        items = [stored.metadata for key, stored in self._objects.items() if key.startswith(object_prefix)]
        return sorted(items, key=lambda item: item.virtual_path)

    def delete(self, user_id: str, thread_id: str, virtual_path: str) -> None:
        object_key = artifact_object_key(prefix=self._prefix, user_id=user_id, thread_id=thread_id, virtual_path=virtual_path)
        self._objects.pop(object_key, None)


class S3ArtifactStore:
    """S3-compatible ArtifactStore implementation for runtime object mode."""

    def __init__(self, *, config: ObjectStoreConfig, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _make_s3_client(config)

    def put_bytes(
        self,
        user_id: str,
        thread_id: str,
        virtual_path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ArtifactMetadata:
        payload = bytes(data)
        if len(payload) > self._config.max_single_object_bytes:
            raise ValueError(f"runtime artifact exceeds max_single_object_bytes={self._config.max_single_object_bytes}")

        object_key = artifact_object_key(prefix=self._config.prefix, user_id=user_id, thread_id=thread_id, virtual_path=virtual_path)
        parsed = parse_artifact_virtual_path(virtual_path)
        digest = hashlib.sha256(payload).hexdigest()
        s3_metadata = {str(key): str(value) for key, value in (metadata or {}).items()}
        s3_metadata[DEERFLOW_SHA256_METADATA] = digest

        put_kwargs: dict[str, object] = {
            "Bucket": self._config.bucket,
            "Key": object_key,
            "Body": payload,
            "Metadata": s3_metadata,
        }
        if content_type is not None:
            put_kwargs["ContentType"] = content_type
        self._client.put_object(**put_kwargs)

        return ArtifactMetadata(
            object_key=object_key,
            virtual_path=parsed.virtual_path,
            size=len(payload),
            sha256=digest,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )

    def get_bytes(self, user_id: str, thread_id: str, virtual_path: str) -> bytes:
        object_key = artifact_object_key(prefix=self._config.prefix, user_id=user_id, thread_id=thread_id, virtual_path=virtual_path)
        response = self._client.get_object(Bucket=self._config.bucket, Key=object_key)
        return bytes(response["Body"].read())

    def list_files(self, user_id: str, thread_id: str, virtual_prefix: str) -> list[ArtifactMetadata]:
        object_prefix = artifact_object_prefix(prefix=self._config.prefix, user_id=user_id, thread_id=thread_id, virtual_prefix=virtual_prefix)
        object_prefix = f"{object_prefix.rstrip('/')}/"
        items: list[ArtifactMetadata] = []
        continuation_token: str | None = None
        while True:
            kwargs: dict[str, object] = {
                "Bucket": self._config.bucket,
                "Prefix": object_prefix,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = self._client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                key = str(obj["Key"])
                if key.endswith("/"):
                    continue
                head = self._client.head_object(Bucket=self._config.bucket, Key=key)
                items.append(_metadata_from_s3_head(key=key, head=head, prefix=self._config.prefix, user_id=user_id, thread_id=thread_id))
            if not response.get("IsTruncated"):
                break
            continuation_token = str(response.get("NextContinuationToken") or "")
            if not continuation_token:
                break
        return sorted(items, key=lambda item: item.virtual_path)

    def delete(self, user_id: str, thread_id: str, virtual_path: str) -> None:
        object_key = artifact_object_key(prefix=self._config.prefix, user_id=user_id, thread_id=thread_id, virtual_path=virtual_path)
        self._client.delete_object(Bucket=self._config.bucket, Key=object_key)


def make_artifact_store(config: RuntimeStorageConfig, *, s3_client: Any | None = None) -> ArtifactStore | None:
    if config.backend == "filesystem":
        return None
    if config.object_store is None:
        raise ValueError("runtime_storage.object_store is required when runtime_storage.backend is object")
    return S3ArtifactStore(config=config.object_store, client=s3_client)


def _parse_user_data_path(virtual_path: str, *, allow_root: bool) -> ParsedArtifactPath:
    relative = virtual_path.removeprefix(USER_DATA_ROOT).lstrip("/")
    if not relative:
        if not allow_root:
            raise ArtifactPathError(f"runtime artifact path must include a user-data scope: {virtual_path}")
        return ParsedArtifactPath(virtual_path=USER_DATA_ROOT, storage_relative_path="user-data", scope="user-data")

    parts = _normalize_relative_parts(relative, allow_root=allow_root)
    scope = parts[0]
    if scope not in USER_DATA_SCOPES:
        raise ArtifactPathError(f"runtime artifact path must use one of {sorted(USER_DATA_SCOPES)}: {virtual_path}")
    if len(parts) == 1 and not allow_root:
        raise ArtifactPathError(f"runtime artifact path must include a file below {USER_DATA_ROOT}/{scope}: {virtual_path}")
    joined = "/".join(parts)
    return ParsedArtifactPath(virtual_path=f"{USER_DATA_ROOT}/{joined}", storage_relative_path=f"user-data/{joined}", scope=scope)


def _parse_acp_workspace_path(virtual_path: str, *, allow_root: bool) -> ParsedArtifactPath:
    relative = virtual_path.removeprefix(ACP_WORKSPACE_ROOT).lstrip("/")
    if not relative:
        if not allow_root:
            raise ArtifactPathError(f"runtime artifact path must include a file below {ACP_WORKSPACE_ROOT}: {virtual_path}")
        return ParsedArtifactPath(virtual_path=ACP_WORKSPACE_ROOT, storage_relative_path="acp-workspace", scope="acp-workspace")
    parts = _normalize_relative_parts(relative, allow_root=allow_root)
    joined = "/".join(parts)
    return ParsedArtifactPath(virtual_path=f"{ACP_WORKSPACE_ROOT}/{joined}", storage_relative_path=f"acp-workspace/{joined}", scope="acp-workspace")


def _normalize_relative_parts(relative_path: str, *, allow_root: bool) -> tuple[str, ...]:
    if not relative_path:
        if allow_root:
            return ()
        raise ArtifactPathError("runtime artifact path must include a file")
    parts = tuple(relative_path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ArtifactPathError(f"runtime artifact path contains an unsafe segment: {relative_path}")
    return parts


def _object_key(*, prefix: str, user_id: str, thread_id: str, storage_relative_path: str) -> str:
    safe_user_id = _validate_key_component("user_id", user_id)
    safe_thread_id = _validate_key_component("thread_id", thread_id)
    base = f"users/{safe_user_id}/threads/{safe_thread_id}/{storage_relative_path}"
    clean_prefix = prefix.strip().strip("/")
    if not clean_prefix:
        return base
    return f"{clean_prefix}/{base}"


def _validate_key_component(name: str, value: str) -> str:
    clean = value.strip()
    if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise ArtifactPathError(f"{name} is not safe for object keys")
    return clean


def _metadata_from_s3_head(*, key: str, head: Mapping[str, object], prefix: str, user_id: str, thread_id: str) -> ArtifactMetadata:
    raw_metadata = {str(k): str(v) for k, v in dict(head.get("Metadata") or {}).items()}
    digest = raw_metadata.pop(DEERFLOW_SHA256_METADATA, "")
    return ArtifactMetadata(
        object_key=key,
        virtual_path=_virtual_path_from_object_key(prefix=prefix, user_id=user_id, thread_id=thread_id, object_key=key),
        size=int(head.get("ContentLength") or 0),
        sha256=digest,
        content_type=str(head["ContentType"]) if head.get("ContentType") is not None else None,
        metadata=raw_metadata,
    )


def _virtual_path_from_object_key(*, prefix: str, user_id: str, thread_id: str, object_key: str) -> str:
    thread_prefix = _object_key(prefix=prefix, user_id=user_id, thread_id=thread_id, storage_relative_path="")
    relative = object_key.removeprefix(thread_prefix)
    if relative == object_key:
        raise ArtifactPathError(f"object key is outside the requested thread prefix: {object_key}")
    relative = relative.lstrip("/")
    if relative.startswith("user-data/"):
        return f"{USER_DATA_ROOT}/{relative.removeprefix('user-data/')}"
    if relative.startswith("acp-workspace/"):
        return f"{ACP_WORKSPACE_ROOT}/{relative.removeprefix('acp-workspace/')}"
    raise ArtifactPathError(f"object key has an unknown runtime artifact scope: {object_key}")


def _make_s3_client(config: ObjectStoreConfig) -> Any:
    try:
        import boto3
        from botocore.config import Config as BotoCoreConfig
    except ImportError as exc:
        raise RuntimeError("runtime_storage.backend=object requires the boto3 dependency for S3-compatible object storage") from exc

    client_kwargs: dict[str, object] = {
        "region_name": config.region,
        "verify": config.tls_verify,
    }
    if config.endpoint_url:
        client_kwargs["endpoint_url"] = config.endpoint_url
    if config.path_style:
        client_kwargs["config"] = BotoCoreConfig(s3={"addressing_style": "path"})

    access_key = os.getenv(config.access_key_env)
    secret_key = os.getenv(config.secret_key_env)
    if bool(access_key) != bool(secret_key):
        raise RuntimeError(f"Both {config.access_key_env} and {config.secret_key_env} must be set for object storage credentials")
    if access_key and secret_key:
        client_kwargs["aws_access_key_id"] = access_key
        client_kwargs["aws_secret_access_key"] = secret_key

    return boto3.client("s3", **client_kwargs)
