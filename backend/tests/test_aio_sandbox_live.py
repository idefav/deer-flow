from __future__ import annotations

import os
import subprocess
import uuid

import pytest

pytestmark = [pytest.mark.live]


def _docker_image_available(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


def test_live_docker_aio_sandbox_materializes_runtime_context_files(tmp_path) -> None:
    """Live smoke for the Docker AIO sandbox file API.

    This is intentionally opt-in because it starts a real container. It proves
    the stateless DB runtime-context materializer can deliver text and binary
    files through the sandbox API rather than relying on bind mounts.
    """
    if os.getenv("DEER_FLOW_RUN_LIVE_AIO_SANDBOX") != "1":
        pytest.skip("Set DEER_FLOW_RUN_LIVE_AIO_SANDBOX=1 to run the live Docker AIO sandbox smoke.")

    from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox
    from deerflow.community.aio_sandbox.aio_sandbox_provider import DEFAULT_IMAGE
    from deerflow.community.aio_sandbox.backend import wait_for_sandbox_ready
    from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend
    from deerflow.sandbox.materializer import SandboxMaterializedFile, SandboxMaterializer, SandboxMaterializerManifest

    image = os.getenv("DEER_FLOW_AIO_SANDBOX_IMAGE", DEFAULT_IMAGE)
    if not _docker_image_available(image):
        pytest.skip(f"Docker image is not available locally: {image}")

    sandbox_id = f"live-context-{uuid.uuid4().hex[:12]}"
    backend = LocalContainerBackend(
        image=image,
        base_port=int(os.getenv("DEER_FLOW_LIVE_AIO_BASE_PORT", "18080")),
        container_prefix="deer-flow-live-smoke",
        config_mounts=[],
        environment={
            "DISABLE_JUPYTER": "true",
            "DISABLE_CODE_SERVER": "true",
        },
    )
    backend._runtime = "docker"
    info = None
    sandbox = None
    try:
        skills_mount = tmp_path / "sandbox-skills"
        skills_mount.mkdir()
        skills_mount.chmod(0o777)
        info = backend.create(
            "thread-live-context",
            sandbox_id,
            extra_mounts=[(str(skills_mount), "/mnt/skills", False)],
        )
        assert wait_for_sandbox_ready(info.sandbox_url, timeout=90), f"Sandbox did not become ready: {info.sandbox_url}"
        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)

        result = SandboxMaterializer(skills_root="/mnt/skills").materialize(
            sandbox,
            SandboxMaterializerManifest(
                files=[
                    SandboxMaterializedFile(path="agent/SOUL.md", content="Live AIO soul"),
                    SandboxMaterializedFile(path="skills/custom/live/SKILL.md", content="Live AIO skill"),
                    SandboxMaterializedFile(path="skills/custom/live/assets/logo.bin", content=b"\x00skill"),
                ],
                revision="live-aio-context",
            ),
        )

        assert result.changed is True
        assert sandbox.read_file("/tmp/deerflow/context/agent/SOUL.md") == "Live AIO soul"
        assert sandbox.read_file("/mnt/skills/custom/live/SKILL.md") == "Live AIO skill"
        output = sandbox.execute_command(
            "python3 -c \"from pathlib import Path; print(Path('/mnt/skills/custom/live/assets/logo.bin').read_bytes().hex())\""
        )
        assert "00736b696c6c" in output
        assert "manifest_hash" in sandbox.read_file("/tmp/deerflow/context/.manifest.json")
    finally:
        if sandbox is not None:
            sandbox.close()
        if info is not None:
            backend.destroy(info)
