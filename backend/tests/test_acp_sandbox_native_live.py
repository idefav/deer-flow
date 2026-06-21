from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.live, pytest.mark.acp_sandbox_native]


def test_acp_sandbox_native_artifact_is_visible_to_active_leader_sandbox() -> None:
    """Opt-in live proof for isolated ACP sandbox artifacts in object runtime mode."""
    if os.getenv("DEER_FLOW_RUN_ACP_SANDBOX_NATIVE") != "1":
        pytest.skip("Set DEER_FLOW_RUN_ACP_SANDBOX_NATIVE=1 to run ACP sandbox-native live gate.")

    from deerflow.artifacts.sandbox_materializer import SandboxArtifactMaterializer
    from deerflow.artifacts.store import ACP_WORKSPACE_ROOT, make_artifact_store
    from deerflow.config import get_app_config
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id
    from deerflow.sandbox import get_sandbox_provider

    config = get_app_config()
    if config.runtime_storage.backend != "object":
        pytest.fail("ACP sandbox-native live gate requires runtime_storage.backend=object.")

    artifact_store = make_artifact_store(config.runtime_storage)
    if artifact_store is None:
        pytest.fail("ACP sandbox-native live gate requires a configured object_store.")

    provider = get_sandbox_provider()
    if not hasattr(provider, "acquire_ephemeral") or not hasattr(provider, "refresh_thread_artifacts"):
        pytest.fail("ACP sandbox-native live gate requires AioSandboxProvider acquire_ephemeral/refresh support.")

    user_id = str(get_effective_user_id())
    thread_id = f"acp-native-live-{uuid.uuid4().hex[:12]}"
    marker_path = f"{ACP_WORKSPACE_ROOT}/marker-{uuid.uuid4().hex[:12]}.txt"
    marker = f"acp sandbox native marker {thread_id}".encode()
    leader_id = None
    acp_id = None

    try:
        leader_id = provider.acquire(thread_id)
        leader = provider.get(leader_id)
        assert leader is not None

        acp_id = provider.acquire_ephemeral("acp-sandbox-native-live")
        acp_sandbox = provider.get(acp_id)
        assert acp_sandbox is not None

        acp_sandbox.create_dir(ACP_WORKSPACE_ROOT, parents=True, exist_ok=True)
        acp_sandbox.update_file(marker_path, marker)
        SandboxArtifactMaterializer(artifact_store).flush_thread(
            user_id,
            thread_id,
            acp_sandbox,
            roots=(ACP_WORKSPACE_ROOT,),
        )

        assert provider.refresh_thread_artifacts(thread_id, roots=(ACP_WORKSPACE_ROOT,)) is True
        assert leader.download_file(marker_path) == marker

        gateway_acp_path = get_paths().acp_workspace_dir(thread_id, user_id=user_id) / marker_path.removeprefix(
            f"{ACP_WORKSPACE_ROOT}/"
        )
        assert not gateway_acp_path.exists()
    finally:
        if acp_id is not None:
            provider.release(acp_id)
        if leader_id is not None:
            provider.release(leader_id)
        try:
            artifact_store.delete(user_id, thread_id, marker_path)
        except Exception:
            pass
