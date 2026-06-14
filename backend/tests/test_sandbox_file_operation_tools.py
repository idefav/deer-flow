"""Tests for sandbox patch and file operation tools."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.sandbox.sandbox import FileMetadata
from deerflow.sandbox.tools import (
    apply_patch_tool,
    copy_file_tool,
    file_info_tool,
    mkdir_tool,
    move_file_tool,
    remove_file_tool,
    str_replace_tool,
    write_file_tool,
)


class MemorySandbox:
    id = "memory-sandbox"

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.dirs: set[str] = {"/mnt/user-data/workspace"}
        self.calls: list[tuple] = []
        self.lock = threading.Lock()

    def _metadata_for(self, path: str) -> FileMetadata:
        if path in self.files:
            return FileMetadata(path=path, exists=True, is_file=True, is_dir=False, size=len(self.files[path].encode()), modified_time=123.0)
        if path in self.dirs:
            return FileMetadata(path=path, exists=True, is_file=False, is_dir=True, size=0, modified_time=123.0)
        return FileMetadata(path=path, exists=False, is_file=False, is_dir=False, size=None, modified_time=None)

    def get_metadata(self, path: str) -> FileMetadata:
        self.calls.append(("metadata", path))
        return self._metadata_for(path)

    def read_file(self, path: str) -> str:
        self.calls.append(("read", path))
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        self.calls.append(("write", path, content, append))
        self.files[path] = self.files.get(path, "") + content if append else content

    def create_dir(self, path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
        self.calls.append(("mkdir", path, parents, exist_ok))
        self.dirs.add(path)

    def remove_file(self, path: str) -> None:
        self.calls.append(("remove", path))
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def move_file(self, source_path: str, dest_path: str, *, overwrite: bool = False) -> None:
        self.calls.append(("move", source_path, dest_path, overwrite))
        if source_path not in self.files:
            raise FileNotFoundError(source_path)
        if dest_path in self.files and not overwrite:
            raise FileExistsError(dest_path)
        self.files[dest_path] = self.files.pop(source_path)

    def copy_file(self, source_path: str, dest_path: str, *, overwrite: bool = False) -> None:
        self.calls.append(("copy", source_path, dest_path, overwrite))
        if source_path not in self.files:
            raise FileNotFoundError(source_path)
        if dest_path in self.files and not overwrite:
            raise FileExistsError(dest_path)
        self.files[dest_path] = self.files[source_path]


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(state={}, context={"thread_id": "thread-1"}, config={})


@pytest.fixture()
def non_local_sandbox(monkeypatch):
    sandbox = MemorySandbox(
        {
            "/mnt/user-data/workspace/app.py": "def value():\n    return 1\n",
            "/mnt/user-data/workspace/old.txt": "delete me\n",
            "/mnt/user-data/workspace/existing.txt": "old content\n",
        }
    )
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: sandbox)
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    monkeypatch.setattr("deerflow.sandbox.tools.is_local_sandbox", lambda runtime: False)
    return sandbox


def test_apply_patch_updates_adds_deletes_and_overwrites_existing_file(non_local_sandbox: MemorySandbox) -> None:
    result = apply_patch_tool.func(
        runtime=_runtime(),
        description="apply multi-file patch",
        patch_text="""*** Begin Patch
*** Update File: /mnt/user-data/workspace/app.py
@@
 def value():
-    return 1
+    return 2
*** Add File: /mnt/user-data/workspace/existing.txt
+new content
*** Delete File: /mnt/user-data/workspace/old.txt
*** End Patch
""",
    )

    assert result.startswith("OK")
    assert non_local_sandbox.files["/mnt/user-data/workspace/app.py"] == "def value():\n    return 2\n"
    assert non_local_sandbox.files["/mnt/user-data/workspace/existing.txt"] == "new content\n"
    assert "/mnt/user-data/workspace/old.txt" not in non_local_sandbox.files


def test_file_tool_descriptions_steer_large_html_edits() -> None:
    assert "HTML" in apply_patch_tool.description
    assert "large files" in apply_patch_tool.description
    assert "Default choice for editing existing text files" in apply_patch_tool.description
    assert "Read the target file first" in apply_patch_tool.description
    assert "*** Update File:" in apply_patch_tool.description
    assert "@@ " in apply_patch_tool.description
    assert "Do not use this tool for binary files" in apply_patch_tool.description
    assert "single exact replacement" in str_replace_tool.description
    assert "large HTML" in write_file_tool.description
    assert "final response" in write_file_tool.description


def test_apply_patch_maps_relative_paths_to_workspace(monkeypatch) -> None:
    sandbox = MemorySandbox({"/mnt/user-data/workspace/src/app.py": "name = 'old'\n"})
    runtime = _runtime()
    thread_data = {
        "workspace_path": "/host/thread/workspace",
        "uploads_path": "/host/thread/uploads",
        "outputs_path": "/host/thread/outputs",
    }
    runtime.state = {"thread_data": thread_data}

    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: sandbox)
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    monkeypatch.setattr("deerflow.sandbox.tools.is_local_sandbox", lambda runtime: True)
    monkeypatch.setattr("deerflow.sandbox.tools.get_thread_data", lambda runtime: thread_data)
    monkeypatch.setattr("deerflow.sandbox.tools.validate_local_tool_path", lambda path, thread_data, read_only=False: None)
    monkeypatch.setattr("deerflow.sandbox.tools._resolve_and_validate_user_data_path", lambda path, thread_data: path)

    result = apply_patch_tool.func(
        runtime=runtime,
        description="patch relative path",
        patch_text="""*** Begin Patch
*** Update File: src/app.py
@@
-name = 'old'
+name = 'new'
*** End Patch
""",
    )

    assert result.startswith("OK")
    assert sandbox.files["/mnt/user-data/workspace/src/app.py"] == "name = 'new'\n"


def test_apply_patch_preflights_all_changes_before_writing(non_local_sandbox: MemorySandbox) -> None:
    result = apply_patch_tool.func(
        runtime=_runtime(),
        description="patch should fail before partial writes",
        patch_text="""*** Begin Patch
*** Add File: /mnt/user-data/workspace/new.txt
+created
*** Update File: /mnt/user-data/workspace/app.py
@@
-missing old line
+replacement
*** End Patch
""",
    )

    assert result.startswith("Error:")
    assert "/mnt/user-data/workspace/new.txt" not in non_local_sandbox.files
    assert all(call[0] != "write" for call in non_local_sandbox.calls)


def test_apply_patch_inserts_after_context_anchor(non_local_sandbox: MemorySandbox) -> None:
    non_local_sandbox.files["/mnt/user-data/workspace/anchor.txt"] = "first\nanchor\nlast\n"

    result = apply_patch_tool.func(
        runtime=_runtime(),
        description="insert after anchor",
        patch_text="""*** Begin Patch
*** Update File: /mnt/user-data/workspace/anchor.txt
@@ anchor
+inserted
*** End Patch
""",
    )

    assert result.startswith("OK")
    assert non_local_sandbox.files["/mnt/user-data/workspace/anchor.txt"] == "first\nanchor\ninserted\nlast\n"


def test_apply_patch_end_of_file_marker_appends_at_end(non_local_sandbox: MemorySandbox) -> None:
    non_local_sandbox.files["/mnt/user-data/workspace/notes.txt"] = "first\n"

    result = apply_patch_tool.func(
        runtime=_runtime(),
        description="append at eof",
        patch_text="""*** Begin Patch
*** Update File: /mnt/user-data/workspace/notes.txt
@@
*** End of File
+tail
*** End Patch
""",
    )

    assert result.startswith("OK")
    assert non_local_sandbox.files["/mnt/user-data/workspace/notes.txt"] == "first\ntail\n"


def test_apply_patch_preserves_missing_trailing_newline(non_local_sandbox: MemorySandbox) -> None:
    non_local_sandbox.files["/mnt/user-data/workspace/no_newline.txt"] = "name = 'old'"

    result = apply_patch_tool.func(
        runtime=_runtime(),
        description="replace without adding newline",
        patch_text="""*** Begin Patch
*** Update File: /mnt/user-data/workspace/no_newline.txt
@@
-name = 'old'
+name = 'new'
*** End Patch
""",
    )

    assert result.startswith("OK")
    assert non_local_sandbox.files["/mnt/user-data/workspace/no_newline.txt"] == "name = 'new'"


def test_apply_patch_rejects_ambiguous_pure_insertion(non_local_sandbox: MemorySandbox) -> None:
    non_local_sandbox.files["/mnt/user-data/workspace/ambiguous.txt"] = "first\nlast\n"

    result = apply_patch_tool.func(
        runtime=_runtime(),
        description="reject ambiguous insert",
        patch_text="""*** Begin Patch
*** Update File: /mnt/user-data/workspace/ambiguous.txt
@@
+inserted
*** End Patch
""",
    )

    assert result.startswith("Error:")
    assert non_local_sandbox.files["/mnt/user-data/workspace/ambiguous.txt"] == "first\nlast\n"
    assert all(call[0] != "write" for call in non_local_sandbox.calls)


def test_apply_patch_rejects_same_path_move_without_mutating(non_local_sandbox: MemorySandbox) -> None:
    non_local_sandbox.files["/mnt/user-data/workspace/same.txt"] = "old\n"

    result = apply_patch_tool.func(
        runtime=_runtime(),
        description="reject same path move",
        patch_text="""*** Begin Patch
*** Update File: /mnt/user-data/workspace/same.txt
*** Move to: /mnt/user-data/workspace/same.txt
@@
-old
+new
*** End Patch
""",
    )

    assert result.startswith("Error:")
    assert non_local_sandbox.files["/mnt/user-data/workspace/same.txt"] == "old\n"


def test_file_info_tool_reports_metadata(non_local_sandbox: MemorySandbox) -> None:
    result = file_info_tool.func(
        runtime=_runtime(),
        description="inspect app file",
        path="/mnt/user-data/workspace/app.py",
    )

    assert "file" in result
    assert "size=" in result
    assert "/mnt/user-data/workspace/app.py" in result


def test_file_operation_tools_call_sandbox_methods(non_local_sandbox: MemorySandbox) -> None:
    assert mkdir_tool.func(runtime=_runtime(), description="make dir", path="/mnt/user-data/workspace/pkg").startswith("OK")
    assert copy_file_tool.func(
        runtime=_runtime(),
        description="copy file",
        source_path="/mnt/user-data/workspace/app.py",
        dest_path="/mnt/user-data/workspace/app_copy.py",
        overwrite=True,
    ).startswith("OK")
    assert move_file_tool.func(
        runtime=_runtime(),
        description="move file",
        source_path="/mnt/user-data/workspace/app_copy.py",
        dest_path="/mnt/user-data/workspace/app_moved.py",
        overwrite=True,
    ).startswith("OK")
    assert remove_file_tool.func(
        runtime=_runtime(),
        description="remove file",
        path="/mnt/user-data/workspace/app_moved.py",
    ).startswith("OK")

    assert "/mnt/user-data/workspace/pkg" in non_local_sandbox.dirs
    assert "/mnt/user-data/workspace/app_copy.py" not in non_local_sandbox.files
    assert "/mnt/user-data/workspace/app_moved.py" not in non_local_sandbox.files


def test_local_sandbox_file_operations_respect_virtual_mapping(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = LocalSandbox(
        "local",
        [PathMapping(container_path="/mnt/user-data/workspace", local_path=str(workspace))],
    )

    sandbox.create_dir("/mnt/user-data/workspace/pkg")
    sandbox.write_file("/mnt/user-data/workspace/pkg/app.py", "print('hi')\n")
    metadata = sandbox.get_metadata("/mnt/user-data/workspace/pkg/app.py")
    sandbox.copy_file(
        "/mnt/user-data/workspace/pkg/app.py",
        "/mnt/user-data/workspace/pkg/app_copy.py",
    )
    sandbox.move_file(
        "/mnt/user-data/workspace/pkg/app_copy.py",
        "/mnt/user-data/workspace/pkg/app_moved.py",
    )
    sandbox.remove_file("/mnt/user-data/workspace/pkg/app_moved.py")

    assert metadata.exists is True
    assert metadata.is_file is True
    assert metadata.path == "/mnt/user-data/workspace/pkg/app.py"
    assert (workspace / "pkg" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert not (workspace / "pkg" / "app_moved.py").exists()


def test_local_sandbox_file_operations_reject_read_only_mount(tmp_path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    sandbox = LocalSandbox(
        "local",
        [PathMapping(container_path="/mnt/protected", local_path=str(protected), read_only=True)],
    )

    with pytest.raises(OSError):
        sandbox.create_dir("/mnt/protected/new")
