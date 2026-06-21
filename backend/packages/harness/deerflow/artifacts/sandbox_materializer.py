from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import Protocol

from deerflow.artifacts.store import ACP_WORKSPACE_ROOT, USER_DATA_ROOT, ArtifactMetadata, ArtifactStore

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

    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        max_depth: int = 8,
        max_materialize_files: int | None = None,
        max_materialize_bytes: int | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._max_depth = max_depth
        self._max_materialize_files = max_materialize_files
        self._max_materialize_bytes = max_materialize_bytes

    def materialize_thread(
        self,
        user_id: str,
        thread_id: str,
        sandbox: SandboxFileTransport,
        *,
        roots: tuple[str, ...] = RUNTIME_MATERIALIZE_ROOTS,
    ) -> None:
        items_by_root = self._list_store_items_by_root(user_id, thread_id, roots=roots)
        self._enforce_materialize_budget(items_by_root)

        for root, items in items_by_root.items():
            sandbox.create_dir(root, parents=True, exist_ok=True)
            for item in items:
                parent = str(PurePosixPath(item.virtual_path).parent)
                if parent and parent != ".":
                    sandbox.create_dir(parent, parents=True, exist_ok=True)
                sandbox.update_file(item.virtual_path, self._artifact_store.get_bytes(user_id, thread_id, item.virtual_path))

    def flush_thread(
        self,
        user_id: str,
        thread_id: str,
        sandbox: SandboxFileTransport,
        *,
        roots: tuple[str, ...] = RUNTIME_MATERIALIZE_ROOTS,
    ) -> None:
        for root in roots:
            store_paths = {item.virtual_path for item in self._artifact_store.list_files(user_id, thread_id, root)}
            sandbox_paths: set[str] = set()
            for path in sandbox.list_dir(root, max_depth=self._max_depth):
                if path.rstrip("/") == root.rstrip("/"):
                    continue
                try:
                    content = sandbox.download_file(path)
                except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
                    continue
                content_type, _ = mimetypes.guess_type(path)
                self._artifact_store.put_bytes(user_id, thread_id, path, content, content_type=content_type)
                sandbox_paths.add(path)
            for stale_path in sorted(store_paths - sandbox_paths):
                self._artifact_store.delete(user_id, thread_id, stale_path)

    def _list_store_items_by_root(
        self,
        user_id: str,
        thread_id: str,
        *,
        roots: tuple[str, ...],
    ) -> dict[str, list[ArtifactMetadata]]:
        return {root: self._artifact_store.list_files(user_id, thread_id, root) for root in roots}

    def _enforce_materialize_budget(self, items_by_root: dict[str, list[ArtifactMetadata]]) -> None:
        items = [item for root_items in items_by_root.values() for item in root_items]
        if self._max_materialize_files is not None and len(items) > self._max_materialize_files:
            raise RuntimeError(
                f"runtime artifact materialization would load {len(items)} files, "
                f"exceeding max_materialize_files={self._max_materialize_files}"
            )

        total_bytes = sum(item.size for item in items)
        if self._max_materialize_bytes is not None and total_bytes > self._max_materialize_bytes:
            raise RuntimeError(
                f"runtime artifact materialization would load {total_bytes} bytes, "
                f"exceeding max_materialize_bytes={self._max_materialize_bytes}"
            )
