"""Regression tests for provisioner PVC volume support."""

import pytest

# ── _build_volumes ─────────────────────────────────────────────────────


class TestBuildVolumes:
    """Tests for _build_volumes: PVC vs hostPath selection."""

    def test_default_uses_hostpath_for_skills(self, provisioner_module):
        """When SKILLS_PVC_NAME is empty, skills volume should use hostPath."""
        provisioner_module.SKILLS_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1")
        skills_vol = volumes[0]
        assert skills_vol.host_path is not None
        assert skills_vol.host_path.path == provisioner_module.SKILLS_HOST_PATH
        assert skills_vol.host_path.type == "Directory"
        assert skills_vol.persistent_volume_claim is None

    def test_default_uses_hostpath_for_userdata(self, provisioner_module):
        """When USERDATA_PVC_NAME is empty, user-data volume should use hostPath."""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1")
        userdata_vol = volumes[1]
        assert userdata_vol.host_path is not None
        assert userdata_vol.persistent_volume_claim is None

    def test_hostpath_userdata_includes_thread_id(self, provisioner_module):
        """hostPath user-data path should include thread_id."""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("my-thread-42")
        userdata_vol = volumes[1]
        path = userdata_vol.host_path.path
        assert "my-thread-42" in path
        assert path.endswith("user-data")
        assert userdata_vol.host_path.type == "DirectoryOrCreate"

    def test_skills_pvc_overrides_hostpath(self, provisioner_module):
        """When SKILLS_PVC_NAME is set, skills volume should use PVC."""
        provisioner_module.SKILLS_PVC_NAME = "my-skills-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        skills_vol = volumes[0]
        assert skills_vol.persistent_volume_claim is not None
        assert skills_vol.persistent_volume_claim.claim_name == "my-skills-pvc"
        assert skills_vol.persistent_volume_claim.read_only is True
        assert skills_vol.host_path is None

    def test_userdata_pvc_overrides_hostpath(self, provisioner_module):
        """When USERDATA_PVC_NAME is set, user-data volume should use PVC."""
        provisioner_module.USERDATA_PVC_NAME = "my-userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        userdata_vol = volumes[1]
        assert userdata_vol.persistent_volume_claim is not None
        assert userdata_vol.persistent_volume_claim.claim_name == "my-userdata-pvc"
        assert userdata_vol.host_path is None

    def test_both_pvc_set(self, provisioner_module):
        """When both PVC names are set, both volumes use PVC."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        assert volumes[0].persistent_volume_claim is not None
        assert volumes[1].persistent_volume_claim is not None

    def test_returns_two_volumes(self, provisioner_module):
        """Should always return exactly two volumes."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        assert len(provisioner_module._build_volumes("t")) == 2

        provisioner_module.SKILLS_PVC_NAME = "a"
        provisioner_module.USERDATA_PVC_NAME = "b"
        assert len(provisioner_module._build_volumes("t")) == 2

    def test_volume_names_are_stable(self, provisioner_module):
        """Volume names must stay 'skills' and 'user-data'."""
        volumes = provisioner_module._build_volumes("thread-1")
        assert volumes[0].name == "skills"
        assert volumes[1].name == "user-data"

    def test_extra_mount_replaces_default_skills_volume(self, provisioner_module):
        """Writable DB-mode /mnt/skills mount should override the default read-only skills volume."""
        extra_mounts = [
            provisioner_module.ExtraMount(
                host_path="/host/thread/skills",
                container_path="/mnt/skills",
                read_only=False,
            )
        ]

        volumes = provisioner_module._build_volumes("thread-1", extra_mounts=extra_mounts)

        assert [volume.name for volume in volumes] == ["user-data", "extra-mount-0"]
        assert volumes[1].host_path.path == "/host/thread/skills"
        assert volumes[1].host_path.type == "DirectoryOrCreate"

    def test_object_runtime_omits_userdata_volume(self, provisioner_module):
        """Object runtime storage must not create a user-data PVC or hostPath volume."""
        provisioner_module.RUNTIME_STORAGE_BACKEND = "object"
        provisioner_module.USERDATA_PVC_NAME = ""

        volumes = provisioner_module._build_volumes("thread-1")

        assert [volume.name for volume in volumes] == ["skills"]

    def test_object_runtime_rejects_userdata_pvc(self, provisioner_module):
        """Object runtime storage must fail closed if USERDATA_PVC_NAME is still set."""
        provisioner_module.RUNTIME_STORAGE_BACKEND = "object"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"

        with pytest.raises(RuntimeError, match="USERDATA_PVC_NAME"):
            provisioner_module._build_volumes("thread-1")

    def test_object_runtime_rejects_extra_userdata_mount(self, provisioner_module):
        """Object runtime storage must not accept request-scoped user-data mounts."""
        provisioner_module.RUNTIME_STORAGE_BACKEND = "object"
        extra_mounts = [
            provisioner_module.ExtraMount(
                host_path="/host/thread/user-data",
                container_path="/mnt/user-data",
                read_only=False,
            )
        ]

        with pytest.raises(RuntimeError, match="/mnt/user-data"):
            provisioner_module._build_volumes("thread-1", extra_mounts=extra_mounts)


# ── _build_volume_mounts ───────────────────────────────────────────────


class TestBuildVolumeMounts:
    """Tests for _build_volume_mounts: mount paths and subPath behavior."""

    def test_default_no_subpath(self, provisioner_module):
        """hostPath mode should not set sub_path on user-data mount."""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        userdata_mount = mounts[1]
        assert userdata_mount.sub_path is None

    def test_pvc_sets_user_scoped_subpath(self, provisioner_module):
        """PVC mode should include user_id in the user-data subPath."""
        provisioner_module.USERDATA_PVC_NAME = "my-pvc"
        mounts = provisioner_module._build_volume_mounts("thread-42", user_id="user-7")
        userdata_mount = mounts[1]
        assert userdata_mount.sub_path == "deer-flow/users/user-7/threads/thread-42/user-data"

    def test_pvc_defaults_to_default_user_subpath(self, provisioner_module):
        """Older callers should still land under a stable default user namespace."""
        provisioner_module.USERDATA_PVC_NAME = "my-pvc"
        mounts = provisioner_module._build_volume_mounts("thread-42")
        userdata_mount = mounts[1]
        assert userdata_mount.sub_path == "deer-flow/users/default/threads/thread-42/user-data"

    def test_skills_mount_read_only(self, provisioner_module):
        """Skills mount should always be read-only."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[0].read_only is True

    def test_userdata_mount_read_write(self, provisioner_module):
        """User-data mount should always be read-write."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[1].read_only is False

    def test_mount_paths_are_stable(self, provisioner_module):
        """Mount paths must stay /mnt/skills and /mnt/user-data."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[0].mount_path == "/mnt/skills"
        assert mounts[1].mount_path == "/mnt/user-data"

    def test_mount_names_match_volumes(self, provisioner_module):
        """Mount names should match the volume names."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[0].name == "skills"
        assert mounts[1].name == "user-data"

    def test_returns_two_mounts(self, provisioner_module):
        """Should always return exactly two mounts."""
        assert len(provisioner_module._build_volume_mounts("t")) == 2

    def test_extra_mount_replaces_default_skills_mount(self, provisioner_module):
        """Writable DB-mode /mnt/skills mount should replace the default read-only skills mount."""
        extra_mounts = [
            provisioner_module.ExtraMount(
                host_path="/host/thread/skills",
                container_path="/mnt/skills",
                read_only=False,
            )
        ]

        mounts = provisioner_module._build_volume_mounts("thread-1", extra_mounts=extra_mounts)

        assert [mount.mount_path for mount in mounts] == ["/mnt/user-data", "/mnt/skills"]
        assert mounts[1].name == "extra-mount-0"
        assert mounts[1].read_only is False

    def test_object_runtime_omits_userdata_mount(self, provisioner_module):
        provisioner_module.RUNTIME_STORAGE_BACKEND = "object"
        provisioner_module.USERDATA_PVC_NAME = ""

        mounts = provisioner_module._build_volume_mounts("thread-1")

        assert [mount.mount_path for mount in mounts] == ["/mnt/skills"]


# ── _build_pod integration ─────────────────────────────────────────────


class TestBuildPodVolumes:
    """Integration: _build_pod should wire volumes and mounts correctly."""

    def test_pod_spec_has_volumes(self, provisioner_module):
        """Pod spec should contain exactly 2 volumes."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        assert len(pod.spec.volumes) == 2

    def test_pod_spec_has_volume_mounts(self, provisioner_module):
        """Container should have exactly 2 volume mounts."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        assert len(pod.spec.containers[0].volume_mounts) == 2

    def test_pod_pvc_mode_uses_user_scoped_subpath(self, provisioner_module):
        """Pod should use a user-scoped subPath for PVC user-data."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod("sandbox-1", "thread-1", user_id="user-7")
        assert pod.spec.volumes[0].persistent_volume_claim is not None
        assert pod.spec.volumes[1].persistent_volume_claim is not None
        userdata_mount = pod.spec.containers[0].volume_mounts[1]
        assert userdata_mount.sub_path == "deer-flow/users/user-7/threads/thread-1/user-data"

    def test_pod_wires_extra_mounts(self, provisioner_module):
        """Pod should mount provisioner extra_mounts with the requested permissions."""
        extra_mounts = [
            provisioner_module.ExtraMount(
                host_path="/host/thread/skills",
                container_path="/mnt/skills",
                read_only=False,
            )
        ]

        pod = provisioner_module._build_pod("sandbox-1", "thread-1", extra_mounts=extra_mounts)

        assert [volume.name for volume in pod.spec.volumes] == ["user-data", "extra-mount-0"]
        assert [mount.mount_path for mount in pod.spec.containers[0].volume_mounts] == ["/mnt/user-data", "/mnt/skills"]
        assert pod.spec.containers[0].volume_mounts[1].read_only is False

    def test_object_runtime_pod_has_no_userdata_volume_or_mount(self, provisioner_module):
        provisioner_module.RUNTIME_STORAGE_BACKEND = "object"
        provisioner_module.USERDATA_PVC_NAME = ""

        pod = provisioner_module._build_pod("sandbox-1", "thread-1")

        assert [volume.name for volume in pod.spec.volumes] == ["skills"]
        assert [mount.mount_path for mount in pod.spec.containers[0].volume_mounts] == ["/mnt/skills"]

    def test_pod_records_mount_contract_hash(self, provisioner_module):
        """Pod annotations should record the mount contract used for idempotent reuse."""
        extra_mounts = [
            provisioner_module.ExtraMount(
                host_path="/host/thread/skills",
                container_path="/mnt/skills",
                read_only=False,
            )
        ]

        pod = provisioner_module._build_pod("sandbox-1", "thread-1", extra_mounts=extra_mounts)

        annotations = pod.metadata.annotations
        assert annotations[provisioner_module.MOUNT_CONTRACT_HASH_ANNOTATION] == provisioner_module._mount_contract_hash(extra_mounts)
        assert annotations[provisioner_module.MOUNT_CONTRACT_PATHS_ANNOTATION] == "/mnt/skills"


# ── create_sandbox integration ─────────────────────────────────────────


@pytest.mark.anyio
async def test_create_sandbox_passes_extra_mounts_to_pod_builder(monkeypatch, provisioner_module):
    """POST model should pass extra_mounts through to Pod construction."""
    captured: dict[str, object] = {}
    node_port_calls = {"count": 0}

    class FakeCoreV1:
        def create_namespaced_pod(self, namespace, pod):
            captured["pod_namespace"] = namespace
            captured["pod"] = pod

        def create_namespaced_service(self, namespace, service):
            captured["service_namespace"] = namespace
            captured["service"] = service

    def fake_get_node_port(_sandbox_id):
        node_port_calls["count"] += 1
        return None if node_port_calls["count"] == 1 else 31001

    def fake_build_pod(sandbox_id, thread_id, user_id=provisioner_module.DEFAULT_USER_ID, extra_mounts=None):
        captured["build_pod_args"] = (sandbox_id, thread_id, user_id, extra_mounts)
        return "pod"

    monkeypatch.setattr(provisioner_module, "core_v1", FakeCoreV1())
    monkeypatch.setattr(provisioner_module, "_get_node_port", fake_get_node_port)
    monkeypatch.setattr(provisioner_module, "_get_pod_phase", lambda _sandbox_id: "Running")
    monkeypatch.setattr(provisioner_module, "_build_pod", fake_build_pod)
    monkeypatch.setattr(provisioner_module, "_build_service", lambda _sandbox_id: "service")

    req = provisioner_module.CreateSandboxRequest(
        sandbox_id="sandbox-1",
        thread_id="thread-1",
        user_id="user-7",
        extra_mounts=[
            provisioner_module.ExtraMount(
                host_path="/host/thread/skills",
                container_path="/mnt/skills",
                read_only=False,
            )
        ],
    )

    response = await provisioner_module.create_sandbox(req)

    sandbox_id, thread_id, user_id, extra_mounts = captured["build_pod_args"]
    assert (sandbox_id, thread_id, user_id) == ("sandbox-1", "thread-1", "user-7")
    assert extra_mounts == req.extra_mounts
    assert captured["pod"] == "pod"
    assert captured["service"] == "service"
    assert response.sandbox_url.endswith(":31001")


@pytest.mark.anyio
async def test_create_sandbox_rejects_existing_pod_with_mismatched_mount_contract(monkeypatch, provisioner_module):
    """Existing Pods must not be reused when their mount contract is incompatible."""

    class FakeCoreV1:
        def read_namespaced_pod(self, name, namespace):
            return type(
                "Pod",
                (),
                {
                    "metadata": type(
                        "Metadata",
                        (),
                        {
                            "name": name,
                            "namespace": namespace,
                            "annotations": {
                                provisioner_module.MOUNT_CONTRACT_HASH_ANNOTATION: "old-contract",
                            },
                        },
                    )(),
                },
            )()

    monkeypatch.setattr(provisioner_module, "core_v1", FakeCoreV1())
    monkeypatch.setattr(provisioner_module, "_get_node_port", lambda _sandbox_id: 31001)
    monkeypatch.setattr(provisioner_module, "_get_pod_phase", lambda _sandbox_id: "Running")

    req = provisioner_module.CreateSandboxRequest(
        sandbox_id="sandbox-1",
        thread_id="thread-1",
        extra_mounts=[
            provisioner_module.ExtraMount(
                host_path="/host/thread/skills",
                container_path="/mnt/skills",
                read_only=False,
            )
        ],
    )

    with pytest.raises(provisioner_module.HTTPException) as exc_info:
        await provisioner_module.create_sandbox(req)

    assert exc_info.value.status_code == 409
    assert "mount contract" in exc_info.value.detail


@pytest.mark.anyio
async def test_create_sandbox_reuses_existing_pod_with_matching_mount_contract(monkeypatch, provisioner_module):
    """Existing Pods remain idempotent when their mount contract matches the request."""
    extra_mounts = [
        provisioner_module.ExtraMount(
            host_path="/host/thread/skills",
            container_path="/mnt/skills",
            read_only=False,
        )
    ]

    class FakeCoreV1:
        def read_namespaced_pod(self, name, namespace):
            return type(
                "Pod",
                (),
                {
                    "metadata": type(
                        "Metadata",
                        (),
                        {
                            "name": name,
                            "namespace": namespace,
                            "annotations": {
                                provisioner_module.MOUNT_CONTRACT_HASH_ANNOTATION: provisioner_module._mount_contract_hash(
                                    extra_mounts
                                ),
                            },
                        },
                    )(),
                },
            )()

        def create_namespaced_pod(self, namespace, pod):
            raise AssertionError("matching existing sandbox should not create a new Pod")

        def create_namespaced_service(self, namespace, service):
            raise AssertionError("matching existing sandbox should not create a new Service")

    monkeypatch.setattr(provisioner_module, "core_v1", FakeCoreV1())
    monkeypatch.setattr(provisioner_module, "_get_node_port", lambda _sandbox_id: 31001)
    monkeypatch.setattr(provisioner_module, "_get_pod_phase", lambda _sandbox_id: "Running")

    response = await provisioner_module.create_sandbox(
        provisioner_module.CreateSandboxRequest(
            sandbox_id="sandbox-1",
            thread_id="thread-1",
            extra_mounts=extra_mounts,
        )
    )

    assert response.sandbox_url.endswith(":31001")
    assert response.status == "Running"
