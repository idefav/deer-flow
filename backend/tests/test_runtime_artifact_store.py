import pytest

from deerflow.artifacts.store import ArtifactPathError, InMemoryArtifactStore, S3ArtifactStore, artifact_object_key, make_artifact_store
from deerflow.config.runtime_storage_config import ObjectStoreConfig, RuntimeStorageConfig


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, object]] = {}
        self.put_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> None:
        bucket = str(kwargs["Bucket"])
        key = str(kwargs["Key"])
        body = bytes(kwargs["Body"])
        self.put_calls.append(kwargs)
        self.objects[(bucket, key)] = {
            "Body": body,
            "ContentType": kwargs.get("ContentType"),
            "Metadata": dict(kwargs.get("Metadata") or {}),
        }

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        obj = self.objects[(Bucket, Key)]
        return {
            "Body": _FakeBody(obj["Body"]),
            "ContentType": obj.get("ContentType"),
            "Metadata": dict(obj.get("Metadata") or {}),
            "ContentLength": len(obj["Body"]),
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        obj = self.objects[(Bucket, Key)]
        return {
            "ContentType": obj.get("ContentType"),
            "Metadata": dict(obj.get("Metadata") or {}),
            "ContentLength": len(obj["Body"]),
        }

    def list_objects_v2(self, *, Bucket: str, Prefix: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        contents = [{"Key": key, "Size": len(obj["Body"])} for (bucket, key), obj in self.objects.items() if bucket == Bucket and key.startswith(Prefix)]
        return {"Contents": sorted(contents, key=lambda item: item["Key"])}

    def delete_object(self, **kwargs: object) -> None:
        self.delete_calls.append(kwargs)
        self.objects.pop((str(kwargs["Bucket"]), str(kwargs["Key"])), None)


def test_artifact_object_key_maps_user_data_scopes_to_thread_prefix():
    key = artifact_object_key(
        prefix="deerflow",
        user_id="user-1",
        thread_id="thread-1",
        virtual_path="/mnt/user-data/workspace/reports/final.md",
    )

    assert key == "deerflow/users/user-1/threads/thread-1/user-data/workspace/reports/final.md"


def test_artifact_object_key_maps_acp_workspace_to_thread_prefix():
    key = artifact_object_key(
        prefix="deerflow",
        user_id="user-1",
        thread_id="thread-1",
        virtual_path="/mnt/acp-workspace/subagent/result.json",
    )

    assert key == "deerflow/users/user-1/threads/thread-1/acp-workspace/subagent/result.json"


@pytest.mark.parametrize(
    "virtual_path",
    [
        "/mnt/user-data/uploads/../secret.txt",
        "/mnt/user-data/workspace",
        "/mnt/acp-workspace/../../secret.txt",
        "/tmp/uploads/file.txt",
    ],
)
def test_artifact_object_key_rejects_paths_outside_runtime_contract(virtual_path):
    with pytest.raises(ArtifactPathError):
        artifact_object_key(
            prefix="deerflow",
            user_id="user-1",
            thread_id="thread-1",
            virtual_path=virtual_path,
        )


def test_in_memory_artifact_store_lists_only_requested_scope_with_metadata():
    store = InMemoryArtifactStore(prefix="deerflow")

    store.put_bytes(
        user_id="user-1",
        thread_id="thread-1",
        virtual_path="/mnt/user-data/uploads/input.txt",
        data=b"hello",
        content_type="text/plain",
    )
    store.put_bytes(
        user_id="user-1",
        thread_id="thread-1",
        virtual_path="/mnt/user-data/outputs/result.txt",
        data=b"output",
    )
    store.put_bytes(
        user_id="user-2",
        thread_id="thread-1",
        virtual_path="/mnt/user-data/uploads/other.txt",
        data=b"other",
    )

    listed = store.list_files("user-1", "thread-1", "/mnt/user-data/uploads")

    assert [item.virtual_path for item in listed] == ["/mnt/user-data/uploads/input.txt"]
    assert listed[0].object_key == "deerflow/users/user-1/threads/thread-1/user-data/uploads/input.txt"
    assert listed[0].size == 5
    assert len(listed[0].sha256) == 64
    assert listed[0].content_type == "text/plain"


def test_in_memory_artifact_store_delete_removes_object():
    store = InMemoryArtifactStore(prefix="deerflow")
    store.put_bytes(
        user_id="user-1",
        thread_id="thread-1",
        virtual_path="/mnt/user-data/uploads/input.txt",
        data=b"hello",
    )

    store.delete("user-1", "thread-1", "/mnt/user-data/uploads/input.txt")

    with pytest.raises(FileNotFoundError):
        store.get_bytes("user-1", "thread-1", "/mnt/user-data/uploads/input.txt")


def test_s3_artifact_store_put_get_list_delete_uses_s3_object_contract():
    client = _FakeS3Client()
    config = ObjectStoreConfig(bucket="deerflow-runtime", endpoint_url="http://seaweedfs-s3:8333")
    store = S3ArtifactStore(config=config, client=client)

    stored = store.put_bytes(
        user_id="user-1",
        thread_id="thread-1",
        virtual_path="/mnt/user-data/uploads/input.txt",
        data=b"hello",
        content_type="text/plain",
        metadata={"source": "upload"},
    )

    assert stored.object_key == "deerflow/users/user-1/threads/thread-1/user-data/uploads/input.txt"
    assert client.put_calls[0]["Bucket"] == "deerflow-runtime"
    assert client.put_calls[0]["Key"] == stored.object_key
    assert client.put_calls[0]["ContentType"] == "text/plain"
    assert client.put_calls[0]["Metadata"]["source"] == "upload"
    assert client.put_calls[0]["Metadata"]["deerflow-sha256"] == stored.sha256
    assert store.get_bytes("user-1", "thread-1", "/mnt/user-data/uploads/input.txt") == b"hello"

    listed = store.list_files("user-1", "thread-1", "/mnt/user-data/uploads")

    assert [item.virtual_path for item in listed] == ["/mnt/user-data/uploads/input.txt"]
    assert listed[0].content_type == "text/plain"
    assert listed[0].metadata == {"source": "upload"}
    assert listed[0].sha256 == stored.sha256

    store.delete("user-1", "thread-1", "/mnt/user-data/uploads/input.txt")

    assert client.delete_calls[0] == {"Bucket": "deerflow-runtime", "Key": stored.object_key}
    assert client.objects == {}


def test_make_artifact_store_returns_s3_store_for_object_backend():
    storage_config = RuntimeStorageConfig(
        backend="object",
        object_store=ObjectStoreConfig(bucket="deerflow-runtime"),
    )

    store = make_artifact_store(storage_config, s3_client=_FakeS3Client())

    assert isinstance(store, S3ArtifactStore)


def test_make_artifact_store_returns_none_for_filesystem_backend():
    assert make_artifact_store(RuntimeStorageConfig()) is None
