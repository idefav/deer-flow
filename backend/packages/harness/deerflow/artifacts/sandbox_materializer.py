from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import Protocol

from deerflow.artifacts.store import ACP_WORKSPACE_ROOT, USER_DATA_ROOT, ArtifactStore

RUNTIME_MATERIALIZE_ROOTS = (
    f"{USER_DATA_ROOT}/workspace",
    f"{USER_DATA_ROOT}/uploads",
    f"{USER_DATA_ROOT}/outputs",
    ACP_WORKSPACE_ROOT,
)


class SandboxFileTransport(Protocol):
    def create_dir(self, path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
        """Create a directory inside the sandbox."""

    def update_file(self, path: str, content: bytes) -> None:
        """Write bytes into the sandbox."""

    def list_dir(self, path: str, max_depth: int = 8) -> list[str]:
        """List files/directories inside the sandbox."""

    def download_file(self, path: str) -> bytes:
        """Download file bytes from the sandbox."""


class SandboxArtifactMaterializer:
    """Synchronize runtime artifacts between object storage and a sandbox."""

    def __init__(self, artifact_store: ArtifactStore, *, max_depth: int = 8) -> None:
        self._artifact_store = artifact_store
        self._max_depth = max_depth

    def materialize_thread(self, user_id: str, thread_id: str, sandbox: SandboxFileTransport) -> None:
        for root in RUNTIME_MATERIALIZE_ROOTS:
            sandbox.create_dir(root, parents=True, exist_ok=True)
            for item in self._artifact_store.list_files(user_id, thread_id, root):
                parent = str(PurePosixPath(item.virtual_path).parent)
                if parent and parent != ".":
                    sandbox.create_dir(parent, parents=True, exist_ok=True)
                sandbox.update_file(item.virtual_path, self._artifact_store.get_bytes(user_id, thread_id, item.virtual_path))

    def flush_thread(self, user_id: str, thread_id: str, sandbox: SandboxFileTransport) -> None:
        for root in RUNTIME_MATERIALIZE_ROOTS:
            for path in sandbox.list_dir(root, max_depth=self._max_depth):
                if path.rstrip("/") == root.rstrip("/"):
                    continue
                try:
                    content = sandbox.download_file(path)
                except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
                    continue
                content_type, _ = mimetypes.guess_type(path)
                self._artifact_store.put_bytes(user_id, thread_id, path, content, content_type=content_type)
