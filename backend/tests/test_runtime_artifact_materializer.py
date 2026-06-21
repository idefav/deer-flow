import pytest

from deerflow.artifacts.sandbox_materializer import SandboxArtifactMaterializer
from deerflow.artifacts.store import ACP_WORKSPACE_ROOT, InMemoryArtifactStore


class FakeSandbox:
    def __init__(self) -> None:
        self.created_dirs: list[str] = []
        self.files: dict[str, bytes] = {}

    def create_dir(self, path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
        assert parents is True
        assert exist_ok is True
        self.created_dirs.append(path)

    def update_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def list_dir(self, path: str, max_depth: int = 8) -> list[str]:
        assert max_depth == 8
        return [item for item in self.files if item == path or item.startswith(f"{path.rstrip('/')}/")]

    def download_file(self, path: str) -> bytes:
        return self.files[path]


def test_materialize_thread_downloads_object_store_files_to_sandbox_paths():
    store = InMemoryArtifactStore(prefix="deerflow")
    store.put_bytes("user-1", "thread-1", "/mnt/user-data/uploads/input.txt", b"input")
    store.put_bytes("user-1", "thread-1", "/mnt/user-data/workspace/app.py", b"print('hi')\n")
    store.put_bytes("user-1", "thread-1", "/mnt/acp-workspace/subagent/result.json", b"{}")
    sandbox = FakeSandbox()

    SandboxArtifactMaterializer(store).materialize_thread("user-1", "thread-1", sandbox)

    assert "/mnt/user-data/workspace" in sandbox.created_dirs
    assert "/mnt/user-data/uploads" in sandbox.created_dirs
    assert "/mnt/user-data/outputs" in sandbox.created_dirs
    assert "/mnt/acp-workspace" in sandbox.created_dirs
    assert sandbox.files["/mnt/user-data/uploads/input.txt"] == b"input"
    assert sandbox.files["/mnt/user-data/workspace/app.py"] == b"print('hi')\n"
    assert sandbox.files["/mnt/acp-workspace/subagent/result.json"] == b"{}"


def test_flush_thread_uploads_sandbox_files_back_to_object_store():
    store = InMemoryArtifactStore(prefix="deerflow")
    sandbox = FakeSandbox()
    sandbox.files["/mnt/user-data/outputs/result.txt"] = b"result"
    sandbox.files["/mnt/user-data/workspace/app.py"] = b"print('done')\n"
    sandbox.files["/mnt/acp-workspace/subagent/result.json"] = b'{"ok": true}'

    SandboxArtifactMaterializer(store).flush_thread("user-1", "thread-1", sandbox)

    assert store.get_bytes("user-1", "thread-1", "/mnt/user-data/outputs/result.txt") == b"result"
    assert store.get_bytes("user-1", "thread-1", "/mnt/user-data/workspace/app.py") == b"print('done')\n"
    assert store.get_bytes("user-1", "thread-1", "/mnt/acp-workspace/subagent/result.json") == b'{"ok": true}'


def test_flush_thread_deletes_object_store_files_missing_from_sandbox():
    store = InMemoryArtifactStore(prefix="deerflow")
    store.put_bytes("user-1", "thread-1", "/mnt/user-data/outputs/stale.txt", b"stale")
    store.put_bytes("user-1", "thread-1", "/mnt/acp-workspace/old.txt", b"old")
    sandbox = FakeSandbox()
    sandbox.files["/mnt/user-data/outputs/current.txt"] = b"current"

    SandboxArtifactMaterializer(store).flush_thread("user-1", "thread-1", sandbox)

    with pytest.raises(FileNotFoundError):
        store.get_bytes("user-1", "thread-1", "/mnt/user-data/outputs/stale.txt")
    with pytest.raises(FileNotFoundError):
        store.get_bytes("user-1", "thread-1", "/mnt/acp-workspace/old.txt")
    assert store.get_bytes("user-1", "thread-1", "/mnt/user-data/outputs/current.txt") == b"current"


def test_flush_thread_can_scope_roots_without_deleting_unselected_paths():
    store = InMemoryArtifactStore(prefix="deerflow")
    store.put_bytes("user-1", "thread-1", "/mnt/user-data/outputs/leader.txt", b"leader")
    store.put_bytes("user-1", "thread-1", "/mnt/acp-workspace/stale.txt", b"stale")
    sandbox = FakeSandbox()
    sandbox.files["/mnt/acp-workspace/result.txt"] = b"result"

    SandboxArtifactMaterializer(store).flush_thread(
        "user-1",
        "thread-1",
        sandbox,
        roots=(ACP_WORKSPACE_ROOT,),
    )

    assert store.get_bytes("user-1", "thread-1", "/mnt/user-data/outputs/leader.txt") == b"leader"
    assert store.get_bytes("user-1", "thread-1", "/mnt/acp-workspace/result.txt") == b"result"
    with pytest.raises(FileNotFoundError):
        store.get_bytes("user-1", "thread-1", "/mnt/acp-workspace/stale.txt")


def test_materialize_thread_rejects_object_store_file_count_over_budget():
    store = InMemoryArtifactStore(prefix="deerflow")
    store.put_bytes("user-1", "thread-1", "/mnt/user-data/uploads/one.txt", b"one")
    store.put_bytes("user-1", "thread-1", "/mnt/user-data/uploads/two.txt", b"two")
    sandbox = FakeSandbox()

    with pytest.raises(RuntimeError, match="max_materialize_files=1"):
        SandboxArtifactMaterializer(store, max_materialize_files=1).materialize_thread("user-1", "thread-1", sandbox)

    assert sandbox.files == {}


def test_materialize_thread_rejects_object_store_bytes_over_budget():
    store = InMemoryArtifactStore(prefix="deerflow")
    store.put_bytes("user-1", "thread-1", "/mnt/user-data/uploads/input.txt", b"12345")
    sandbox = FakeSandbox()

    with pytest.raises(RuntimeError, match="max_materialize_bytes=4"):
        SandboxArtifactMaterializer(store, max_materialize_bytes=4).materialize_thread("user-1", "thread-1", sandbox)

    assert sandbox.files == {}
