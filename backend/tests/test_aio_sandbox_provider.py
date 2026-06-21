"""Tests for AioSandboxProvider mount helpers."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.artifacts.store import ACP_WORKSPACE_ROOT, InMemoryArtifactStore
from deerflow.config.app_config import AppConfig
from deerflow.config.paths import Paths, join_host_path
from deerflow.runtime.user_context import reset_current_user, set_current_user

# ── ensure_thread_dirs ───────────────────────────────────────────────────────


def test_ensure_thread_dirs_creates_acp_workspace(tmp_path):
    """ACP workspace directory must be created alongside user-data dirs."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-1")

    assert (tmp_path / "threads" / "thread-1" / "user-data" / "workspace").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "uploads").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "outputs").exists()
    assert (tmp_path / "threads" / "thread-1" / "acp-workspace").exists()


def test_ensure_thread_dirs_acp_workspace_is_world_writable(tmp_path):
    """ACP workspace must be chmod 0o777 so the ACP subprocess can write into it."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-2")

    acp_dir = tmp_path / "threads" / "thread-2" / "acp-workspace"
    mode = oct(acp_dir.stat().st_mode & 0o777)
    assert mode == oct(0o777)


def test_host_thread_dir_rejects_invalid_thread_id(tmp_path):
    paths = Paths(base_dir=tmp_path)

    with pytest.raises(ValueError, match="Invalid thread_id"):
        paths.host_thread_dir("../escape")


# ── _get_thread_mounts ───────────────────────────────────────────────────────


def _make_provider(tmp_path):
    """Build a minimal AioSandboxProvider instance without starting the idle checker."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    with patch.object(aio_mod.AioSandboxProvider, "_start_idle_checker"):
        provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
        provider._config = {}
        provider._sandboxes = {}
        provider._lock = MagicMock()
        provider._idle_checker_stop = MagicMock()
    return provider


def test_get_thread_mounts_includes_acp_workspace(tmp_path, monkeypatch):
    """_get_thread_mounts must include /mnt/acp-workspace (read-only) for docker sandbox."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-3")

    container_paths = {m[1]: (m[0], m[2]) for m in mounts}

    assert "/mnt/acp-workspace" in container_paths, "ACP workspace mount is missing"
    expected_host = str(tmp_path / "threads" / "thread-3" / "acp-workspace")
    actual_host, read_only = container_paths["/mnt/acp-workspace"]
    assert actual_host == expected_host
    assert read_only is True, "ACP workspace should be read-only inside the sandbox"


def test_get_thread_mounts_includes_user_data_dirs(tmp_path, monkeypatch):
    """Baseline: user-data mounts must still be present after the ACP workspace change."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-4")
    container_paths = {m[1] for m in mounts}

    assert "/mnt/user-data/workspace" in container_paths
    assert "/mnt/user-data/uploads" in container_paths
    assert "/mnt/user-data/outputs" in container_paths


def test_get_thread_mounts_object_runtime_skips_user_data_and_acp_mounts(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
            "runtime_storage": {
                "backend": "object",
                "object_store": {"bucket": "deerflow-runtime"},
            },
        }
    )

    monkeypatch.setattr(aio_mod, "get_app_config", lambda: config)
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-object")

    assert mounts == []
    assert not (tmp_path / "threads" / "thread-object").exists()


def test_create_sandbox_materializes_object_runtime_after_ready(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._warm_pool = {}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._config = {"replicas": 3}
    provider._backend = SimpleNamespace(
        create=MagicMock(return_value=aio_mod.SandboxInfo(sandbox_id="sandbox-object", sandbox_url="http://sandbox")),
        destroy=MagicMock(),
    )
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_get_extra_mounts", lambda _self, _thread_id: [])
    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_materialize_thread_artifacts", lambda _self, thread_id, sandbox_id: calls.append((thread_id, sandbox_id)))
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda _url, timeout=60: True)

    sandbox_id = provider._create_sandbox("thread-object", "sandbox-object")

    assert sandbox_id == "sandbox-object"
    assert calls == [("thread-object", "sandbox-object")]


def test_discover_or_create_object_runtime_uses_advisory_lock_without_thread_dirs(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock = aio_mod.AioSandboxProvider._discover_or_create_with_lock.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )
    provider._thread_sandboxes = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._sandboxes = {}
    provider._last_activity = {}
    provider._backend = SimpleNamespace(discover=MagicMock(return_value=None))
    config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
            "database": {"backend": "postgres", "postgres_url": "postgresql://user:pass@db/deerflow"},
            "runtime_storage": {
                "backend": "object",
                "object_store": {"bucket": "deerflow-runtime"},
            },
        }
    )
    lock_events: list[str] = []

    class FakeLock:
        def __enter__(self):
            lock_events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            lock_events.append("exit")

    paths = MagicMock()
    paths.ensure_thread_dirs.side_effect = AssertionError("object mode must not create lock-file directories")

    monkeypatch.setattr(aio_mod, "get_app_config", lambda: config)
    monkeypatch.setattr(aio_mod, "get_paths", lambda: paths)
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: "user-1")
    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_sandbox_creation_lock", lambda _self, thread_id, sandbox_id: FakeLock())
    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_create_sandbox", lambda _self, thread_id, sandbox_id: "sandbox-object")

    sandbox_id = provider._discover_or_create_with_lock("thread-object", "sandbox-object")

    assert sandbox_id == "sandbox-object"
    assert lock_events == ["enter", "exit"]
    paths.ensure_thread_dirs.assert_not_called()


def test_release_flushes_object_runtime_before_closing_sandbox(tmp_path, monkeypatch):
    provider, sandbox, aio_mod = _make_provider_with_active_sandbox(tmp_path, "sandbox-rel")
    provider._thread_sandboxes = {"thread-object": "sandbox-rel"}
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_flush_thread_artifacts", lambda _self, thread_id, sandbox_obj: calls.append((thread_id, sandbox_obj)))

    provider.release("sandbox-rel")

    assert calls == [("thread-object", sandbox)]
    sandbox.close.assert_called_once_with()


def test_destroy_flushes_object_runtime_before_closing_sandbox(tmp_path, monkeypatch):
    provider, sandbox, aio_mod = _make_provider_with_active_sandbox(tmp_path, "sandbox-destroy")
    provider._thread_sandboxes = {"thread-object": "sandbox-destroy"}
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_flush_thread_artifacts", lambda _self, thread_id, sandbox_obj: calls.append((thread_id, sandbox_obj)))

    provider.destroy("sandbox-destroy")

    assert calls == [("thread-object", sandbox)]
    sandbox.close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()


def test_shutdown_flushes_object_runtime_before_destroying_active_sandboxes(tmp_path, monkeypatch):
    provider, sandbox, aio_mod = _make_provider_with_active_sandbox(tmp_path, "sandbox-shutdown")
    provider._thread_sandboxes = {"thread-object": "sandbox-shutdown"}
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_flush_thread_artifacts", lambda _self, thread_id, sandbox_obj: calls.append((thread_id, sandbox_obj)))

    provider.shutdown()

    assert calls == [("thread-object", sandbox)]
    sandbox.close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()


def test_drop_unhealthy_sandbox_flushes_object_runtime_before_close(tmp_path, monkeypatch):
    provider, sandbox, aio_mod = _make_provider_with_active_sandbox(tmp_path, "sandbox-unhealthy")
    provider._thread_sandboxes = {"thread-object": "sandbox-unhealthy"}
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_flush_thread_artifacts", lambda _self, thread_id, sandbox_obj: calls.append((thread_id, sandbox_obj)))

    provider._drop_unhealthy_sandbox("sandbox-unhealthy", "failed health check")

    assert calls == [("thread-object", sandbox)]
    sandbox.close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()


def test_destroy_flushes_warm_pool_object_runtime_before_backend_destroy(tmp_path, monkeypatch):
    provider, _sandbox, aio_mod = _make_provider_with_active_sandbox(tmp_path, "sandbox-warm")
    info = provider._sandbox_infos.pop("sandbox-warm")
    provider._sandboxes = {}
    provider._thread_sandboxes = {}
    provider._warm_pool = {"sandbox-warm": (info, 0.0, ("thread-object",))}
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_flush_thread_artifacts", lambda _self, thread_id, sandbox_obj: calls.append((thread_id, sandbox_obj.id)))

    provider.destroy("sandbox-warm")

    assert calls == [("thread-object", "sandbox-warm")]
    provider._backend.destroy.assert_called_once_with(info)


def test_drop_unhealthy_sandbox_flushes_warm_pool_object_runtime_before_backend_destroy(tmp_path, monkeypatch):
    provider, _sandbox, aio_mod = _make_provider_with_active_sandbox(tmp_path, "sandbox-warm-unhealthy")
    info = provider._sandbox_infos.pop("sandbox-warm-unhealthy")
    provider._sandboxes = {}
    provider._thread_sandboxes = {}
    provider._warm_pool = {"sandbox-warm-unhealthy": (info, 0.0, ("thread-object",))}
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_flush_thread_artifacts", lambda _self, thread_id, sandbox_obj: calls.append((thread_id, sandbox_obj.id)))

    provider._drop_unhealthy_sandbox("sandbox-warm-unhealthy", "failed health check", expected_info=info)

    assert calls == [("thread-object", "sandbox-warm-unhealthy")]
    provider._backend.destroy.assert_called_once_with(info)


def test_uses_thread_data_mounts_is_false_for_local_backend_object_runtime(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._backend = aio_mod.LocalContainerBackend(
        image="sandbox-image",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
            "runtime_storage": {
                "backend": "object",
                "object_store": {"bucket": "deerflow-runtime"},
            },
        }
    )

    monkeypatch.setattr(aio_mod, "get_app_config", lambda: config)

    assert provider.uses_thread_data_mounts is False


def test_materialize_thread_artifacts_passes_object_store_budget_config(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-budget")
    captured: dict[str, object] = {}
    config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
            "runtime_storage": {
                "backend": "object",
                "object_store": {
                    "bucket": "deerflow-runtime",
                    "max_materialize_files": 7,
                    "max_materialize_bytes": 99,
                },
            },
        }
    )
    artifact_store = object()

    class FakeMaterializer:
        def __init__(self, store, **kwargs):
            captured["store"] = store
            captured["kwargs"] = kwargs

        def materialize_thread(self, user_id, thread_id, sandbox_obj, *, roots=None):
            captured["materialize"] = (user_id, thread_id, sandbox_obj)
            captured["roots"] = roots

    monkeypatch.setattr(aio_mod, "get_app_config", lambda: config)
    monkeypatch.setattr(aio_mod, "make_artifact_store", lambda _runtime_storage: artifact_store)
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: "user-1")
    monkeypatch.setattr(aio_mod, "SandboxArtifactMaterializer", FakeMaterializer)

    provider._materialize_thread_artifacts("thread-object", "sandbox-budget")

    assert captured == {
        "store": artifact_store,
        "kwargs": {
            "max_materialize_files": 7,
            "max_materialize_bytes": 99,
        },
        "materialize": ("user-1", "thread-object", sandbox),
        "roots": None,
    }


def test_refresh_thread_artifacts_materializes_active_sandbox_acp_root_only(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-active")
    provider._thread_sandboxes = {"thread-object": "sandbox-active"}
    config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
            "runtime_storage": {
                "backend": "object",
                "object_store": {"bucket": "deerflow-runtime"},
            },
        }
    )
    store = InMemoryArtifactStore(prefix="deerflow")
    store.put_bytes("user-1", "thread-object", f"{ACP_WORKSPACE_ROOT}/result.txt", b"result")
    store.put_bytes("user-1", "thread-object", "/mnt/user-data/outputs/leader.txt", b"leader")
    sandbox.files = {}

    def create_dir(path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
        sandbox.files.setdefault(path, None)

    def update_file(path: str, content: bytes) -> None:
        sandbox.files[path] = content

    sandbox.create_dir.side_effect = create_dir
    sandbox.update_file.side_effect = update_file

    monkeypatch.setattr(aio_mod, "get_app_config", lambda: config)
    monkeypatch.setattr(aio_mod, "make_artifact_store", lambda _runtime_storage: store)
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: "user-1")

    refreshed = provider.refresh_thread_artifacts("thread-object", roots=(ACP_WORKSPACE_ROOT,))

    assert refreshed is True
    assert sandbox.files[f"{ACP_WORKSPACE_ROOT}/result.txt"] == b"result"
    assert "/mnt/user-data/outputs/leader.txt" not in sandbox.files
    assert provider.refresh_thread_artifacts("missing-thread", roots=(ACP_WORKSPACE_ROOT,)) is False


def test_get_thread_mounts_can_include_writable_db_skills_dir(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts(
        "thread-db",
        include_db_skills_mount=True,
        skills_container_path="/mnt/skills",
    )
    container_paths = {container_path: (host_path, read_only) for host_path, container_path, read_only in mounts}

    expected_host = str(tmp_path / "threads" / "thread-db" / "skills")
    assert container_paths["/mnt/skills"] == (expected_host, False)
    assert (tmp_path / "threads" / "thread-db" / "skills").exists()
    assert oct((tmp_path / "threads" / "thread-db" / "skills").stat().st_mode & 0o777) == oct(0o777)


def test_get_extra_mounts_uses_writable_db_skills_mount(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    config = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills"))

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)
    monkeypatch.setattr(aio_mod, "get_app_config", lambda: config)
    monkeypatch.setattr(aio_mod, "is_db_config_enabled", lambda: True)
    monkeypatch.setattr(
        aio_mod,
        "get_or_new_skill_storage",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("file-backed skills mount should not be used in DB mode")),
    )

    mounts = aio_mod.AioSandboxProvider._get_extra_mounts(provider, "thread-db")
    container_paths = {container_path: (host_path, read_only) for host_path, container_path, read_only in mounts}

    assert container_paths["/mnt/skills"] == (str(tmp_path / "threads" / "thread-db" / "skills"), False)


def test_sandbox_config_accepts_ephemeral_profiles():
    from deerflow.config.sandbox_config import SandboxConfig

    config = SandboxConfig.model_validate(
        {
            "use": "deerflow.community.aio_sandbox:AioSandboxProvider",
            "ephemeral_profiles": {
                "codex": {
                    "image": "registry.local/codex-acp:latest",
                    "setup_commands": ["npm install -g @zed-industries/codex-acp"],
                    "environment": {"OPENAI_API_KEY": "$OPENAI_API_KEY"},
                }
            },
        }
    )

    profile = config.ephemeral_profiles["codex"]
    assert profile.image == "registry.local/codex-acp:latest"
    assert profile.setup_commands == ["npm install -g @zed-industries/codex-acp"]
    assert profile.environment == {"OPENAI_API_KEY": "$OPENAI_API_KEY"}


def test_acquire_ephemeral_uses_named_profile_without_thread_cache(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._shutdown_called = False
    provider._idle_checker_thread = None
    provider._config = {
        "replicas": 3,
        "image": "default-aio:latest",
        "ephemeral_profiles": {
            "codex": SimpleNamespace(
                image="registry.local/codex-acp:latest",
                setup_commands=["echo setup"],
                environment={"ACP_TOKEN": "token"},
            )
        },
    }
    created: list[dict[str, object]] = []
    fake_sandboxes: dict[str, MagicMock] = {}

    class FakeBackend:
        destroy = MagicMock()

        def create(self, thread_id, sandbox_id, extra_mounts=None, options=None):
            created.append(
                {
                    "thread_id": thread_id,
                    "sandbox_id": sandbox_id,
                    "extra_mounts": extra_mounts,
                    "options": options,
                }
            )
            return aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url="http://sandbox")

    def fake_aio_sandbox(id, base_url):
        sandbox = MagicMock()
        sandbox.id = id
        sandbox.base_url = base_url

        def fake_execute_command(command: str) -> str:
            marker = next(part.split(":$status", 1)[0] for part in command.split() if part.startswith("__DEERFLOW_SETUP_EXIT__:"))
            return f"{marker}:0"

        sandbox.execute_command = MagicMock(side_effect=fake_execute_command)
        sandbox.close = MagicMock()
        fake_sandboxes[id] = sandbox
        return sandbox

    provider._backend = FakeBackend()
    monkeypatch.setattr(aio_mod, "AioSandbox", fake_aio_sandbox)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda _url, timeout=60: True)

    sandbox_id = provider.acquire_ephemeral("codex")

    assert sandbox_id in fake_sandboxes
    assert provider._thread_sandboxes == {}
    assert sandbox_id in provider._sandboxes
    assert sandbox_id not in provider._warm_pool
    assert created[0]["thread_id"] is None
    assert created[0]["extra_mounts"] is None
    options = created[0]["options"]
    assert options.name == "codex"
    assert options.image == "registry.local/codex-acp:latest"
    assert options.ephemeral is True
    assert options.labels["deerflow.sandbox.kind"] == "ephemeral"
    assert options.labels["deerflow.sandbox.name"] == "codex"
    fake_sandboxes[sandbox_id].execute_command.assert_called_once()
    assert "echo setup" in fake_sandboxes[sandbox_id].execute_command.call_args.args[0]

    provider.release(sandbox_id)

    fake_sandboxes[sandbox_id].close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()
    assert sandbox_id not in provider._warm_pool
    assert sandbox_id not in provider._sandboxes


def test_join_host_path_preserves_windows_drive_letter_style():
    base = r"C:\Users\demo\deer-flow\backend\.deer-flow"

    joined = join_host_path(base, "threads", "thread-9", "user-data", "outputs")

    assert joined == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-9\user-data\outputs"


def test_get_thread_mounts_preserves_windows_host_path_style(tmp_path, monkeypatch):
    """Docker bind mount sources must keep Windows-style paths intact."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setenv("DEER_FLOW_HOST_BASE_DIR", r"C:\Users\demo\deer-flow\backend\.deer-flow")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-10")

    container_paths = {container_path: host_path for host_path, container_path, _ in mounts}

    assert container_paths["/mnt/user-data/workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\workspace"
    assert container_paths["/mnt/user-data/uploads"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\uploads"
    assert container_paths["/mnt/user-data/outputs"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\outputs"
    assert container_paths["/mnt/acp-workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\acp-workspace"


def test_get_skills_mount_uses_active_skill_storage_root(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    configured_skills_dir = tmp_path / "configured-skills"
    configured_skills_dir.mkdir()
    storage_root = tmp_path / "db-materialized-skills"
    storage_root.mkdir()
    config = SimpleNamespace(
        skills=SimpleNamespace(container_path="/mnt/skills", get_skills_path=lambda: configured_skills_dir),
    )
    storage = SimpleNamespace(get_skills_root_path=lambda: storage_root)
    monkeypatch.delenv("DEER_FLOW_HOST_SKILLS_PATH", raising=False)

    monkeypatch.setattr(aio_mod, "get_app_config", lambda: config)
    monkeypatch.setattr(aio_mod, "get_or_new_skill_storage", lambda **_kwargs: storage, raising=False)

    mount = aio_mod.AioSandboxProvider._get_skills_mount()

    assert mount == (str(storage_root), "/mnt/skills", True)


def test_discover_or_create_only_unlocks_when_lock_succeeds(tmp_path, monkeypatch):
    """Unlock should not run if exclusive locking itself fails."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock = aio_mod.AioSandboxProvider._discover_or_create_with_lock.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(
        aio_mod,
        "_lock_file_exclusive",
        lambda _lock_file: (_ for _ in ()).throw(RuntimeError("lock failed")),
    )

    unlock_calls: list[object] = []
    monkeypatch.setattr(
        aio_mod,
        "_unlock_file",
        lambda lock_file: unlock_calls.append(lock_file),
    )

    with patch.object(provider, "_create_sandbox", return_value="sandbox-id"):
        with pytest.raises(RuntimeError, match="lock failed"):
            provider._discover_or_create_with_lock("thread-5", "sandbox-5")

    assert unlock_calls == []


@pytest.mark.anyio
async def test_acquire_async_uses_async_readiness_polling(monkeypatch):
    """AioSandboxProvider async creation must not use sync readiness polling."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(None)
    provider._config = {"replicas": 3}
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=MagicMock(return_value=aio_mod.SandboxInfo(sandbox_id="sandbox-async", sandbox_url="http://sandbox")),
        destroy=MagicMock(),
        discover=MagicMock(return_value=None),
    )

    async_readiness_calls: list[tuple[str, int]] = []

    async def fake_wait_for_sandbox_ready_async(sandbox_url: str, timeout: int = 30, poll_interval: float = 1.0) -> bool:
        async_readiness_calls.append((sandbox_url, timeout))
        return True

    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", fake_wait_for_sandbox_ready_async)
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync readiness should not be used")),
    )

    sandbox_id = await provider._create_sandbox_async("thread-async", "sandbox-async")

    assert sandbox_id == "sandbox-async"
    assert async_readiness_calls == [("http://sandbox", 60)]
    assert provider._backend.destroy.call_count == 0
    assert provider._thread_sandboxes["thread-async"] == "sandbox-async"


@pytest.mark.anyio
async def test_discover_or_create_with_lock_async_offloads_lock_file_open_and_close(tmp_path, monkeypatch):
    """Async lock path must not open or close lock files on the event loop."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock_async = aio_mod.AioSandboxProvider._discover_or_create_with_lock_async.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {"thread-async-lock": "sandbox-async-lock"}
    provider._sandboxes = {"sandbox-async-lock": aio_mod.AioSandbox(id="sandbox-async-lock", base_url="http://sandbox")}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(discover=MagicMock(return_value=None))

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))

    to_thread_calls: list[object] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fake_to_thread)

    sandbox_id = await provider._discover_or_create_with_lock_async("thread-async-lock", "sandbox-async-lock")

    assert sandbox_id == "sandbox-async-lock"
    assert aio_mod._open_lock_file in to_thread_calls
    assert any(getattr(func, "__name__", "") == "close" for func in to_thread_calls)


@pytest.mark.anyio
async def test_acquire_thread_lock_async_uses_dedicated_executor(monkeypatch):
    """Per-thread lock waits should not consume the default asyncio.to_thread pool."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    lock = aio_mod.threading.Lock()

    async def fail_to_thread(*_args, **_kwargs):
        raise AssertionError("thread-lock acquisition must not use asyncio.to_thread")

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fail_to_thread)

    await aio_mod._acquire_thread_lock_async(lock)
    try:
        assert not lock.acquire(blocking=False)
    finally:
        lock.release()


@pytest.mark.anyio
async def test_acquire_async_cancellation_does_not_leak_thread_lock(tmp_path):
    """Cancelled async lock waiters must not leave the per-thread lock held."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()

    thread_id = "thread-cancel-lock"
    thread_lock = provider._get_thread_lock(thread_id)
    thread_lock.acquire()

    task = asyncio.create_task(provider.acquire_async(thread_id))
    await asyncio.sleep(0.05)
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass

    thread_lock.release()
    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        acquired = thread_lock.acquire(blocking=False)
        if acquired:
            thread_lock.release()
            return
        await asyncio.sleep(0.01)

    pytest.fail("provider thread lock was leaked after cancelling acquire_async")


@pytest.mark.anyio
async def test_acquire_async_cancelled_waiter_does_not_block_successor(tmp_path, monkeypatch):
    """A cancelled waiter must not prevent the next live waiter from acquiring."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()

    async def fake_acquire_internal_async(thread_id: str | None) -> str:
        assert thread_id == "thread-successor-lock"
        await asyncio.sleep(0)
        return "sandbox-successor"

    monkeypatch.setattr(provider, "_acquire_internal_async", fake_acquire_internal_async)

    thread_id = "thread-successor-lock"
    thread_lock = provider._get_thread_lock(thread_id)
    thread_lock.acquire()

    cancelled_waiter = asyncio.create_task(provider.acquire_async(thread_id))
    await asyncio.sleep(0.05)
    cancelled_waiter.cancel()
    try:
        await cancelled_waiter
    except asyncio.CancelledError:
        pass

    live_waiter = asyncio.create_task(provider.acquire_async(thread_id))
    thread_lock.release()

    assert await asyncio.wait_for(live_waiter, timeout=1) == "sandbox-successor"

    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        acquired = thread_lock.acquire(blocking=False)
        if acquired:
            thread_lock.release()
            return
        await asyncio.sleep(0.01)

    pytest.fail("provider thread lock was not released after successor acquire_async")


@pytest.mark.anyio
async def test_acquire_internal_async_offloads_cached_reuse_health_check(tmp_path, monkeypatch):
    """Async cached reuse must keep backend health checks off the event loop."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider, _sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-cached-async")
    provider._thread_sandboxes = {"thread-cached-async": "sandbox-cached-async"}
    provider._backend.is_alive = MagicMock(return_value=True)

    to_thread_calls: list[tuple[object, tuple[object, ...]]] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append((func, args))
        return func(*args, **kwargs)

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fake_to_thread)

    sandbox_id = await provider._acquire_internal_async("thread-cached-async")

    assert sandbox_id == "sandbox-cached-async"
    assert to_thread_calls == [(provider._reuse_in_process_sandbox, ("thread-cached-async",))]


def test_remote_backend_create_forwards_effective_user_id(monkeypatch):
    """Provisioner mode must receive user_id so PVC subPath matches user isolation."""
    remote_mod = importlib.import_module("deerflow.community.aio_sandbox.remote_backend")
    backend = remote_mod.RemoteSandboxBackend("http://provisioner:8002")
    token = set_current_user(SimpleNamespace(id="user-7"))
    posted: dict = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sandbox_url": "http://sandbox.local"}

    def _post(url, json, timeout):  # noqa: A002 - mirrors requests.post kwarg
        posted.update({"url": url, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(remote_mod.requests, "post", _post)

    try:
        backend.create("thread-42", "sandbox-42")
    finally:
        reset_current_user(token)

    assert posted["url"] == "http://provisioner:8002/api/sandboxes"
    assert posted["json"] == {
        "sandbox_id": "sandbox-42",
        "thread_id": "thread-42",
        "user_id": "user-7",
    }


def test_remote_backend_create_forwards_extra_mounts(monkeypatch):
    """Provisioner mode must receive mount requirements such as writable DB skills path."""
    remote_mod = importlib.import_module("deerflow.community.aio_sandbox.remote_backend")
    backend = remote_mod.RemoteSandboxBackend("http://provisioner:8002")
    posted: dict = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sandbox_url": "http://sandbox.local"}

    def _post(url, json, timeout):  # noqa: A002 - mirrors requests.post kwarg
        posted.update({"url": url, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(remote_mod.requests, "post", _post)
    monkeypatch.setattr(remote_mod, "get_effective_user_id", lambda: "user-7")

    backend.create(
        "thread-42",
        "sandbox-42",
        extra_mounts=[
            ("/host/thread/skills", "/mnt/skills", False),
            ("/host/thread/acp-workspace", "/mnt/acp-workspace", True),
        ],
    )

    assert posted["json"]["extra_mounts"] == [
        {
            "host_path": "/host/thread/skills",
            "container_path": "/mnt/skills",
            "read_only": False,
        },
        {
            "host_path": "/host/thread/acp-workspace",
            "container_path": "/mnt/acp-workspace",
            "read_only": True,
        },
    ]


# ── Sandbox client teardown (#2872) ──────────────────────────────────────────


def _make_provider_with_active_sandbox(tmp_path, sandbox_id: str):
    """Build a provider with one active sandbox suitable for release/destroy/shutdown tests."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._warm_pool = {}
    provider._sandbox_infos = {
        sandbox_id: aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url="http://sandbox-host"),
    }
    provider._thread_sandboxes = {}
    provider._last_activity = {sandbox_id: 0.0}
    provider._shutdown_called = False
    provider._idle_checker_thread = None
    provider._backend = SimpleNamespace(destroy=MagicMock())

    sandbox = MagicMock()
    sandbox.id = sandbox_id
    sandbox.close = MagicMock()
    provider._sandboxes = {sandbox_id: sandbox}
    return provider, sandbox, aio_mod


def test_release_closes_cached_sandbox_client(tmp_path):
    """release() must close the host-side client owned by the cached AioSandbox (#2872)."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-rel")

    provider.release("sandbox-rel")

    sandbox.close.assert_called_once_with()
    # And the sandbox is parked in the warm pool (container still running).
    assert "sandbox-rel" in provider._warm_pool
    assert "sandbox-rel" not in provider._sandboxes


def test_destroy_closes_cached_sandbox_client(tmp_path):
    """destroy() must close the host-side client before backend container teardown (#2872)."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-destroy")
    backend_destroy = provider._backend.destroy

    provider.destroy("sandbox-destroy")

    sandbox.close.assert_called_once_with()
    backend_destroy.assert_called_once()
    assert "sandbox-destroy" not in provider._sandboxes
    assert "sandbox-destroy" not in provider._sandbox_infos


def test_shutdown_closes_all_active_sandbox_clients(tmp_path):
    """shutdown() must close every cached AioSandbox client during teardown (#2872)."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-shut")

    provider.shutdown()

    sandbox.close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()
    assert provider._sandboxes == {}


def test_release_swallows_close_errors(tmp_path, caplog):
    """A failure inside sandbox.close() must not break provider release()."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-rel-err")
    sandbox.close.side_effect = RuntimeError("boom")

    with caplog.at_level("WARNING"):
        provider.release("sandbox-rel-err")

    assert "Error closing sandbox sandbox-rel-err during release" in caplog.text
    # Still moved to warm pool: client teardown failure must not block lifecycle.
    assert "sandbox-rel-err" in provider._warm_pool


def test_get_uses_in_memory_registry_only(tmp_path):
    """get() must stay event-loop safe by avoiding backend health checks."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-dead")
    provider._backend.is_alive = MagicMock(side_effect=AssertionError("get must not call backend health checks"))

    assert provider.get("sandbox-dead") is sandbox


def test_acquire_drops_dead_cached_sandbox(tmp_path, monkeypatch):
    """acquire() must replace a stale active cache entry after its container dies."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-dead")
    provider._thread_locks = {}
    provider._thread_sandboxes = {"thread-dead": "sandbox-dead"}
    provider._config = {"replicas": 3}
    provider._backend.is_alive = MagicMock(return_value=False)
    provider._backend.discover = MagicMock(return_value=None)
    provider._backend.create = MagicMock(
        return_value=aio_mod.SandboxInfo(
            sandbox_id="sandbox-dead",
            sandbox_url="http://fresh-sandbox",
            container_name="deer-flow-sandbox-sandbox-dead",
        )
    )

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_sandbox_id_for_thread", lambda _self, _thread_id: "sandbox-dead")
    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_get_extra_mounts", lambda _self, _thread_id: [])
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda _url, timeout=60: True)

    sandbox_id = provider.acquire("thread-dead")

    assert sandbox_id == "sandbox-dead"
    sandbox.close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()
    provider._backend.create.assert_called_once()
    assert provider._thread_sandboxes["thread-dead"] == "sandbox-dead"
    assert provider._sandboxes["sandbox-dead"].base_url == "http://fresh-sandbox"


def test_acquire_keeps_cached_sandbox_when_health_check_errors(tmp_path):
    """Transient backend health-check errors must not destroy a tracked sandbox."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-transient")
    provider._thread_locks = {}
    provider._thread_sandboxes = {"thread-transient": "sandbox-transient"}
    provider._backend.is_alive = MagicMock(side_effect=OSError("docker daemon busy"))

    sandbox_id = provider.acquire("thread-transient")

    assert sandbox_id == "sandbox-transient"
    sandbox.close.assert_not_called()
    provider._backend.destroy.assert_not_called()
    assert provider._sandboxes["sandbox-transient"] is sandbox


def test_drop_unhealthy_sandbox_skips_recreated_entry(tmp_path):
    """A stale health-check result must not delete a newly registered sandbox."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._warm_pool = {}
    provider._last_activity = {"sandbox-toctou": 1.0}
    provider._thread_sandboxes = {"thread-toctou": "sandbox-toctou"}
    old_info = aio_mod.SandboxInfo(sandbox_id="sandbox-toctou", sandbox_url="http://old-sandbox")
    new_info = aio_mod.SandboxInfo(sandbox_id="sandbox-toctou", sandbox_url="http://new-sandbox")
    new_sandbox = MagicMock()
    provider._sandbox_infos = {"sandbox-toctou": new_info}
    provider._sandboxes = {"sandbox-toctou": new_sandbox}
    provider._backend = SimpleNamespace(destroy=MagicMock())

    provider._drop_unhealthy_sandbox("sandbox-toctou", "stale health check", expected_info=old_info)

    new_sandbox.close.assert_not_called()
    provider._backend.destroy.assert_not_called()
    assert provider._sandbox_infos["sandbox-toctou"] is new_info
    assert provider._sandboxes["sandbox-toctou"] is new_sandbox
    assert provider._thread_sandboxes == {"thread-toctou": "sandbox-toctou"}


def test_acquire_skips_dead_warm_pool_sandbox(tmp_path, monkeypatch):
    """acquire() must create a fresh sandbox when the warm-pool entry died."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._thread_locks = {}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {
        "sandbox-warm-dead": (
            aio_mod.SandboxInfo(
                sandbox_id="sandbox-warm-dead",
                sandbox_url="http://stale-sandbox",
                container_name="deer-flow-sandbox-sandbox-warm-dead",
            ),
            0.0,
        )
    }
    provider._config = {"replicas": 3}
    provider._backend = SimpleNamespace(
        is_alive=MagicMock(return_value=False),
        destroy=MagicMock(),
        discover=MagicMock(return_value=None),
        create=MagicMock(
            return_value=aio_mod.SandboxInfo(
                sandbox_id="sandbox-warm-dead",
                sandbox_url="http://fresh-sandbox",
                container_name="deer-flow-sandbox-sandbox-warm-dead",
            )
        ),
    )

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_sandbox_id_for_thread", lambda _self, _thread_id: "sandbox-warm-dead")
    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_get_extra_mounts", lambda _self, _thread_id: [])
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda _url, timeout=60: True)

    sandbox_id = provider.acquire("thread-warm-dead")

    assert sandbox_id == "sandbox-warm-dead"
    provider._backend.destroy.assert_called_once()
    provider._backend.create.assert_called_once()
    assert provider._warm_pool == {}
    assert provider._thread_sandboxes["thread-warm-dead"] == "sandbox-warm-dead"
    assert provider._sandboxes["sandbox-warm-dead"].base_url == "http://fresh-sandbox"


def test_destroy_swallows_close_errors_and_still_destroys_backend(tmp_path, caplog):
    """A failure in sandbox.close() must not skip backend container destruction."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-dest-err")
    sandbox.close.side_effect = RuntimeError("boom")

    with caplog.at_level("WARNING"):
        provider.destroy("sandbox-dest-err")

    assert "Error closing sandbox sandbox-dest-err during destroy" in caplog.text
    provider._backend.destroy.assert_called_once()
