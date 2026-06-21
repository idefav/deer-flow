from __future__ import annotations

import os
import uuid
from collections.abc import Mapping

import pytest

pytestmark = [pytest.mark.live, pytest.mark.remote_live]


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"Set {name} to run the remote provisioner AIO sandbox smoke.")
    return value


def _invalid_remote_live_env_message(env: Mapping[str, str]) -> str | None:
    from check_stateless_live_gates import _remote_live_env_issues

    issues = _remote_live_env_issues(env)
    if not issues:
        return None
    return "; ".join(
        f"{issue['name']} expected {issue['expected']}, got {issue['actual']}"
        for issue in issues
    )


def test_remote_provisioner_aio_sandbox_materializes_runtime_context_files() -> None:
    """Opt-in live smoke for a real provisioner/K8s AIO sandbox.

    This test intentionally requires operator-provided environment variables
    because the host path must be valid on the Kubernetes node that runs the
    sandbox Pod.
    """
    if os.getenv("DEER_FLOW_RUN_REMOTE_AIO_SANDBOX") != "1":
        pytest.skip("Set DEER_FLOW_RUN_REMOTE_AIO_SANDBOX=1 to run the remote provisioner AIO sandbox smoke.")

    invalid_env = _invalid_remote_live_env_message(os.environ)
    if invalid_env:
        pytest.fail(f"Invalid remote provisioner AIO sandbox smoke environment: {invalid_env}")

    from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox
    from deerflow.community.aio_sandbox.backend import wait_for_sandbox_ready
    from deerflow.community.aio_sandbox.remote_backend import RemoteSandboxBackend
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializer, SandboxMaterializerManifest

    provisioner_url = _required_env("DEER_FLOW_REMOTE_AIO_PROVISIONER_URL")
    sandbox_id = f"remote-context-{uuid.uuid4().hex[:12]}"
    skills_container_path = os.getenv("DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH", "/mnt/skills")
    skills_host_path = os.getenv("DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH")
    if not skills_host_path:
        skills_host_path_prefix = _required_env("DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX")
        skills_host_path = f"{skills_host_path_prefix.rstrip('/')}/{sandbox_id}"

    backend = RemoteSandboxBackend(provisioner_url=provisioner_url)
    info = None
    sandbox = None
    try:
        info = backend.create(
            "thread-remote-context",
            sandbox_id,
            extra_mounts=[(skills_host_path, skills_container_path, False)],
        )
        timeout = int(os.getenv("DEER_FLOW_REMOTE_AIO_READY_TIMEOUT", "120"))
        assert wait_for_sandbox_ready(info.sandbox_url, timeout=timeout), f"Sandbox did not become ready: {info.sandbox_url}"
        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)

        result = SandboxMaterializer(skills_root=skills_container_path).materialize(
            sandbox,
            SandboxMaterializerManifest(
                files=[
                    SandboxMaterializedFile(path="agent/SOUL.md", content="Remote AIO soul"),
                    SandboxMaterializedFile(path="skills/custom/remote-live/SKILL.md", content="Remote AIO skill"),
                    SandboxMaterializedFile(path="skills/custom/remote-live/assets/logo.bin", content=b"\x00remote"),
                ],
                revision="remote-aio-context",
            ),
        )

        assert result.changed is True
        assert sandbox.read_file("/tmp/deerflow/context/agent/SOUL.md") == "Remote AIO soul"
        assert sandbox.read_file(f"{skills_container_path}/custom/remote-live/SKILL.md") == "Remote AIO skill"
        binary_path = f"{skills_container_path}/custom/remote-live/assets/logo.bin"
        output = sandbox.execute_command(
            f"python3 -c \"from pathlib import Path; print(Path({binary_path!r}).read_bytes().hex())\""
        )
        assert "0072656d6f7465" in output
        assert "manifest_hash" in sandbox.read_file("/tmp/deerflow/context/.manifest.json")
    finally:
        if sandbox is not None:
            sandbox.close()
        if info is not None:
            backend.destroy(info)
