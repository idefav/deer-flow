from pathlib import Path

from deerflow.artifacts.store import InMemoryArtifactStore
from scripts.import_runtime_artifacts_to_object_store import import_runtime_artifacts


def test_import_runtime_artifacts_imports_user_thread_files(tmp_path: Path) -> None:
    state_dir = tmp_path / ".deer-flow"
    upload = state_dir / "users" / "alice" / "threads" / "thread-1" / "user-data" / "uploads" / "input.txt"
    output = state_dir / "users" / "alice" / "threads" / "thread-1" / "user-data" / "outputs" / "result.txt"
    acp = state_dir / "users" / "alice" / "threads" / "thread-1" / "acp-workspace" / "notes.md"
    upload.parent.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    acp.parent.mkdir(parents=True)
    upload.write_text("input")
    output.write_text("result")
    acp.write_text("notes")

    store = InMemoryArtifactStore(prefix="deerflow")
    report = import_runtime_artifacts(state_dir=state_dir, artifact_store=store)

    assert report["imported"] == 3
    assert store.get_bytes("alice", "thread-1", "/mnt/user-data/uploads/input.txt") == b"input"
    assert store.get_bytes("alice", "thread-1", "/mnt/user-data/outputs/result.txt") == b"result"
    assert store.get_bytes("alice", "thread-1", "/mnt/acp-workspace/notes.md") == b"notes"


def test_import_runtime_artifacts_dry_run_does_not_write(tmp_path: Path) -> None:
    state_dir = tmp_path / ".deer-flow"
    upload = state_dir / "users" / "alice" / "threads" / "thread-1" / "user-data" / "uploads" / "input.txt"
    upload.parent.mkdir(parents=True)
    upload.write_text("input")

    store = InMemoryArtifactStore(prefix="deerflow")
    report = import_runtime_artifacts(state_dir=state_dir, artifact_store=store, dry_run=True)

    assert report["discovered"] == 1
    assert report["imported"] == 0
    assert store.list_files("alice", "thread-1", "/mnt/user-data/uploads") == []


def test_import_runtime_artifacts_supports_legacy_default_threads(tmp_path: Path) -> None:
    state_dir = tmp_path / ".deer-flow"
    legacy_upload = state_dir / "threads" / "legacy-thread" / "user-data" / "uploads" / "legacy.txt"
    legacy_upload.parent.mkdir(parents=True)
    legacy_upload.write_text("legacy")

    store = InMemoryArtifactStore(prefix="deerflow")
    report = import_runtime_artifacts(state_dir=state_dir, artifact_store=store)

    assert report["imported"] == 1
    assert store.get_bytes("default", "legacy-thread", "/mnt/user-data/uploads/legacy.txt") == b"legacy"
