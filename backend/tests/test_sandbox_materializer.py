from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.memory.storage import DbMemoryStorage
from deerflow.config.agent_store import DbAgentStore
from deerflow.config.database_config import DatabaseConfig
from deerflow.sandbox.exceptions import SandboxRuntimeError
from deerflow.sandbox.sandbox import FileMetadata, Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider, reset_sandbox_provider, set_sandbox_provider
from deerflow.skills.storage.db_skill_storage import DbSkillStorage


class FakeSandbox(Sandbox):
    def __init__(self) -> None:
        super().__init__("fake")
        self.text_files: dict[str, str] = {}
        self.binary_files: dict[str, bytes] = {}
        self.created_dirs: list[str] = []
        self.write_calls: list[tuple[str, str]] = []
        self.update_calls: list[tuple[str, bytes]] = []

    def execute_command(self, command: str) -> str:
        raise NotImplementedError

    def read_file(self, path: str) -> str:
        if path not in self.text_files:
            raise FileNotFoundError(path)
        return self.text_files[path]

    def download_file(self, path: str) -> bytes:
        if path not in self.binary_files:
            raise FileNotFoundError(path)
        return self.binary_files[path]

    def list_dir(self, path: str, max_depth=2) -> list[str]:
        return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        if append:
            content = self.text_files.get(path, "") + content
        self.text_files[path] = content
        self.write_calls.append((path, content))

    def get_metadata(self, path: str) -> FileMetadata:
        return FileMetadata(
            path=path,
            exists=path in self.text_files or path in self.binary_files,
            is_file=path in self.text_files or path in self.binary_files,
            is_dir=False,
        )

    def create_dir(self, path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
        self.created_dirs.append(path)

    def remove_file(self, path: str) -> None:
        self.text_files.pop(path, None)
        self.binary_files.pop(path, None)

    def move_file(self, source_path: str, dest_path: str, *, overwrite: bool = False) -> None:
        raise NotImplementedError

    def copy_file(self, source_path: str, dest_path: str, *, overwrite: bool = False) -> None:
        raise NotImplementedError

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200):
        return [], False

    def grep(
        self,
        path: str,
        pattern: str,
        *,
        glob: str | None = None,
        literal: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ):
        return [], False

    def update_file(self, path: str, content: bytes) -> None:
        self.binary_files[path] = content
        self.update_calls.append((path, content))


class FakeSandboxProvider(SandboxProvider):
    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self.acquired_thread_ids: list[str | None] = []

    def acquire(self, thread_id: str | None = None) -> str:
        self.acquired_thread_ids.append(thread_id)
        return self.sandbox.id

    async def acquire_async(self, thread_id: str | None = None) -> str:
        self.acquired_thread_ids.append(thread_id)
        return self.sandbox.id

    def get(self, sandbox_id: str) -> Sandbox | None:
        if sandbox_id == self.sandbox.id:
            return self.sandbox
        return None

    def release(self, sandbox_id: str) -> None:
        return None


def test_sandbox_materializer_writes_manifest_files() -> None:
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializer, SandboxMaterializerManifest

    sandbox = FakeSandbox()
    manifest = SandboxMaterializerManifest(
        files=[
            SandboxMaterializedFile(path="memory/default.json", content='{"facts": []}'),
            SandboxMaterializedFile(path="agent/SOUL.md", content="agent soul"),
            SandboxMaterializedFile(path="skills/custom/research/assets/logo.bin", content=b"\x00skill"),
        ],
        revision="rev-1",
    )

    result = SandboxMaterializer().materialize(sandbox, manifest)

    assert result.changed is True
    assert "/tmp/deerflow/context/memory" in sandbox.created_dirs
    assert sandbox.text_files["/tmp/deerflow/context/memory/default.json"] == '{"facts": []}'
    assert sandbox.text_files["/tmp/deerflow/context/agent/SOUL.md"] == "agent soul"
    assert sandbox.binary_files["/tmp/deerflow/context/skills/custom/research/assets/logo.bin"] == b"\x00skill"
    assert "/tmp/deerflow/context/.manifest.json" in sandbox.text_files


def test_sandbox_materializer_writes_skill_files_to_prompt_container_path() -> None:
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializer, SandboxMaterializerManifest

    sandbox = FakeSandbox()
    manifest = SandboxMaterializerManifest(
        files=[
            SandboxMaterializedFile(path="skills/custom/research/SKILL.md", content="skill body"),
            SandboxMaterializedFile(path="skills/custom/research/assets/logo.bin", content=b"\x00skill"),
        ],
        revision="rev-1",
    )

    SandboxMaterializer(skills_root="/mnt/skills").materialize(sandbox, manifest)

    assert sandbox.text_files["/mnt/skills/custom/research/SKILL.md"] == "skill body"
    assert sandbox.binary_files["/mnt/skills/custom/research/assets/logo.bin"] == b"\x00skill"
    assert "/tmp/deerflow/context/skills/custom/research/SKILL.md" not in sandbox.text_files


def test_sandbox_materializer_skips_when_manifest_hash_matches() -> None:
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializer, SandboxMaterializerManifest

    sandbox = FakeSandbox()
    materializer = SandboxMaterializer()
    manifest = SandboxMaterializerManifest(
        files=[SandboxMaterializedFile(path="memory/default.json", content='{"facts": []}')],
        revision="rev-1",
    )

    first = materializer.materialize(sandbox, manifest)
    sandbox.write_calls.clear()
    sandbox.update_calls.clear()
    sandbox.created_dirs.clear()

    second = materializer.materialize(sandbox, manifest)

    assert first.changed is True
    assert second.changed is False
    assert sandbox.write_calls == []
    assert sandbox.update_calls == []
    assert sandbox.created_dirs == []


def test_sandbox_materializer_prunes_files_missing_from_new_manifest() -> None:
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializer, SandboxMaterializerManifest

    sandbox = FakeSandbox()
    materializer = SandboxMaterializer()

    materializer.materialize(
        sandbox,
        SandboxMaterializerManifest(
            files=[
                SandboxMaterializedFile(path="memory/old.json", content='{"old": true}'),
                SandboxMaterializedFile(path="memory/keep.json", content='{"keep": true}'),
            ],
            revision="rev-1",
        ),
    )

    second = materializer.materialize(
        sandbox,
        SandboxMaterializerManifest(
            files=[SandboxMaterializedFile(path="memory/keep.json", content='{"keep": true}')],
            revision="rev-2",
        ),
    )

    assert second.changed is True
    assert "/tmp/deerflow/context/memory/old.json" not in sandbox.text_files
    assert sandbox.text_files["/tmp/deerflow/context/memory/keep.json"] == '{"keep": true}'


def test_runtime_context_manifest_builder_reads_db_memory_agent_and_skill(tmp_path: Path) -> None:
    from deerflow.sandbox.materializer import SandboxRuntimeContextManifestBuilder

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    agent_store = DbAgentStore(database_config=database)
    memory_storage = DbMemoryStorage(database_config=database)
    skill_storage = DbSkillStorage(host_path=str(tmp_path / "skills-cache"), database_config=database)

    agent_store.save_user_profile("alice", "Alice profile")
    agent_store.save_agent(
        "alice",
        "research-agent",
        {
            "name": "research-agent",
            "description": "Research helper",
        },
        "Research soul",
    )
    memory_storage.save({"version": "1.0", "facts": [{"text": "global memory"}]}, user_id="alice")
    memory_storage.save(
        {"version": "1.0", "facts": [{"text": "agent memory"}]},
        "research-agent",
        user_id="alice",
    )
    skill_storage.write_custom_skill(
        "research",
        "SKILL.md",
        "---\nname: research\ndescription: Research skill\n---\n",
    )
    skill_storage._write_custom_skill_payload("research", "references/notes.md", "support notes")
    skill_storage._write_custom_skill_payload("research", "assets/logo.bin", b"\x00skill")
    shutil.rmtree(tmp_path / "skills-cache" / "custom")

    manifest = SandboxRuntimeContextManifestBuilder(
        memory_storage=memory_storage,
        agent_store=agent_store,
        skill_storage=skill_storage,
    ).build(user_id="alice", agent_name="research-agent", skill_names=["research"])

    files = {file.path: file.content for file in manifest.files}

    assert json.loads(files["memory/user.json"])["facts"][0]["text"] == "global memory"
    assert json.loads(files["memory/agents/research-agent.json"])["facts"][0]["text"] == "agent memory"
    assert files["agent/USER.md"] == "Alice profile"
    assert files["agent/SOUL.md"] == "Research soul"
    assert files["skills/custom/research/SKILL.md"].startswith("---\nname: research")
    assert files["skills/custom/research/assets/logo.bin"] == b"\x00skill"
    assert files["skills/custom/research/references/notes.md"] == "support notes"
    assert "alice" in manifest.revision
    assert "research-agent" in manifest.revision


def test_runtime_context_manifest_builder_revision_changes_when_content_changes() -> None:
    from deerflow.sandbox.materializer import SandboxRuntimeContextManifestBuilder

    class MutableMemoryStorage:
        def __init__(self) -> None:
            self.fact = "first"

        def load(self, agent_name=None, *, user_id=None):
            return {"version": "1.0", "facts": [{"text": self.fact}]}

    memory_storage = MutableMemoryStorage()
    builder = SandboxRuntimeContextManifestBuilder(memory_storage=memory_storage)

    first = builder.build(user_id="alice")
    memory_storage.fact = "second"
    second = builder.build(user_id="alice")

    assert first.revision != second.revision
    assert "alice" in first.revision
    assert "alice" in second.revision


def test_runtime_context_manifest_builder_reads_default_agent_soul(tmp_path: Path) -> None:
    from deerflow.sandbox.materializer import SandboxRuntimeContextManifestBuilder

    database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))
    agent_store = DbAgentStore(database_config=database)
    agent_store.save_user_profile("alice", "Alice profile")
    agent_store.save_default_agent_soul("alice", "Default soul")

    manifest = SandboxRuntimeContextManifestBuilder(agent_store=agent_store).build(user_id="alice")

    files = {file.path: file.content for file in manifest.files}
    assert files["agent/USER.md"] == "Alice profile"
    assert files["agent/SOUL.md"] == "Default soul"
    assert "agent=None" not in manifest.revision


def test_ensure_sandbox_initialized_materializes_db_runtime_context(monkeypatch) -> None:
    from deerflow.sandbox import tools as tools_module
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializerManifest

    sandbox = FakeSandbox()
    provider = FakeSandboxProvider(sandbox)
    built_args: list[tuple[str, str | None, list[str] | None]] = []

    class FakeBuilder:
        def build(
            self,
            *,
            user_id: str,
            agent_name: str | None = None,
            skill_names: list[str] | None = None,
        ) -> SandboxMaterializerManifest:
            built_args.append((user_id, agent_name, skill_names))
            return SandboxMaterializerManifest(
                files=[
                    SandboxMaterializedFile(path="agent/SOUL.md", content="Research soul"),
                    SandboxMaterializedFile(path="skills/custom/research/SKILL.md", content="Research skill"),
                ],
                revision="test-revision",
            )

    monkeypatch.setattr(tools_module, "is_db_config_enabled", lambda: True, raising=False)
    monkeypatch.setattr(tools_module, "resolve_runtime_user_id", lambda runtime: "alice", raising=False)
    monkeypatch.setattr(tools_module, "_new_runtime_context_manifest_builder", lambda: FakeBuilder(), raising=False)
    set_sandbox_provider(provider)
    try:
        runtime = SimpleNamespace(
            state={},
            context={"thread_id": "thread-1", "agent_name": "research-agent"},
            config={"metadata": {"available_skills": ["research"]}},
        )

        result = tools_module.ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert result is sandbox
    assert provider.acquired_thread_ids == ["thread-1"]
    assert built_args == [("alice", "research-agent", ["research"])]
    assert sandbox.text_files["/tmp/deerflow/context/agent/SOUL.md"] == "Research soul"
    assert sandbox.text_files["/mnt/skills/custom/research/SKILL.md"] == "Research skill"
    assert runtime.context["sandbox_context_files"] == 2
    assert runtime.context["sandbox_context_manifest_hash"]


def test_ensure_sandbox_initialized_skips_runtime_context_materialization_outside_db_mode(monkeypatch) -> None:
    from deerflow.sandbox import tools as tools_module

    sandbox = FakeSandbox()
    provider = FakeSandboxProvider(sandbox)

    def fail_builder():
        raise AssertionError("builder should not be created outside DB config mode")

    monkeypatch.setattr(tools_module, "is_db_config_enabled", lambda: False, raising=False)
    monkeypatch.setattr(tools_module, "_new_runtime_context_manifest_builder", fail_builder, raising=False)
    set_sandbox_provider(provider)
    try:
        runtime = SimpleNamespace(state={}, context={"thread_id": "thread-1"}, config={})

        result = tools_module.ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert result is sandbox
    assert "/tmp/deerflow/context/.manifest.json" not in sandbox.text_files
    assert "sandbox_context_manifest_hash" not in runtime.context


def test_ensure_sandbox_initialized_raises_when_strict_runtime_context_materialization_fails(monkeypatch) -> None:
    from deerflow.sandbox import tools as tools_module

    sandbox = FakeSandbox()
    provider = FakeSandboxProvider(sandbox)

    class FailingBuilder:
        def build(self, **kwargs):
            raise ValueError("db snapshot unavailable")

    monkeypatch.setattr(tools_module, "is_db_config_enabled", lambda: True, raising=False)
    monkeypatch.setattr(tools_module, "_is_runtime_context_fail_closed", lambda: True, raising=False)
    monkeypatch.setattr(tools_module, "_new_runtime_context_manifest_builder", lambda: FailingBuilder(), raising=False)
    set_sandbox_provider(provider)
    runtime = SimpleNamespace(state={}, context={"thread_id": "thread-1"}, config={})
    try:
        with pytest.raises(SandboxRuntimeError, match="Failed to materialize DB runtime context"):
            tools_module.ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert runtime.context["sandbox_context_error"] == "ValueError: db snapshot unavailable"
    assert "sandbox_context_manifest_hash" not in runtime.context


@pytest.mark.anyio
async def test_ensure_sandbox_initialized_async_materializes_db_runtime_context(monkeypatch) -> None:
    from deerflow.sandbox import tools as tools_module
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializerManifest

    sandbox = FakeSandbox()
    provider = FakeSandboxProvider(sandbox)
    built_args: list[tuple[str, str | None, list[str] | None]] = []

    class FakeBuilder:
        def build(
            self,
            *,
            user_id: str,
            agent_name: str | None = None,
            skill_names: list[str] | None = None,
        ) -> SandboxMaterializerManifest:
            built_args.append((user_id, agent_name, skill_names))
            return SandboxMaterializerManifest(
                files=[SandboxMaterializedFile(path="memory/user.json", content='{"facts": []}')],
                revision="async-test-revision",
            )

    monkeypatch.setattr(tools_module, "is_db_config_enabled", lambda: True, raising=False)
    monkeypatch.setattr(tools_module, "resolve_runtime_user_id", lambda runtime: "alice", raising=False)
    monkeypatch.setattr(tools_module, "_new_runtime_context_manifest_builder", lambda: FakeBuilder(), raising=False)
    set_sandbox_provider(provider)
    try:
        runtime = SimpleNamespace(
            state={},
            context={"thread_id": "thread-async"},
            config={"configurable": {"agent_name": "research-agent", "skills": ["research"]}},
        )

        result = await tools_module.ensure_sandbox_initialized_async(runtime)
    finally:
        reset_sandbox_provider()

    assert result is sandbox
    assert provider.acquired_thread_ids == ["thread-async"]
    assert built_args == [("alice", "research-agent", ["research"])]
    assert sandbox.text_files["/tmp/deerflow/context/memory/user.json"] == '{"facts": []}'
    assert runtime.context["sandbox_context_sandbox_id"] == "fake"
