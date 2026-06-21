# Provisioner Extra Mount Consumption Review

Date: 2026-06-20

Reviewed artifacts:

- `docker/provisioner/app.py`
- `backend/tests/test_provisioner_pvc_volumes.py`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: Bundled provisioner now consumes `extra_mounts`

Batch 70 ensured the gateway sends `extra_mounts` to `POST /api/sandboxes`, but the bundled provisioner still ignored that field. Batch 72 adds an `ExtraMount` request model and passes parsed mounts into Pod construction.

The provisioner now renders each request-scoped mount as:

- `V1Volume(name="extra-mount-{index}", hostPath=DirectoryOrCreate)`
- `V1VolumeMount(name="extra-mount-{index}", mountPath=container_path, readOnly=read_only)`

### P1 Fixed: Writable `/mnt/skills` can override the default read-only skills mount

The default provisioner path mounts skills read-only at `/mnt/skills`. DB/stateless mode needs a writable scratch mount there so the materializer can write DB skill files through the AIO file API. Batch 72 lets request-scoped mounts override existing mount paths, so an extra mount targeting `/mnt/skills` replaces the default read-only mount for that Pod.

### P2 Remaining: Remote live proof still depends on a real cluster

Unit tests prove the bundled provisioner renders the correct Pod manifest. They do not prove that a real Kubernetes cluster accepts the hostPath/PVC configuration or that the AIO shell/file API user can write to the resulting mount. A remote provisioner smoke is still required for production rollout.

## Verification

Commands run:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::TestBuildVolumes::test_extra_mount_replaces_default_skills_volume tests/test_provisioner_pvc_volumes.py::TestBuildVolumeMounts::test_extra_mount_replaces_default_skills_mount tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_wires_extra_mounts -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::test_create_sandbox_passes_extra_mounts_to_pod_builder -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py tests/test_provisioner_kubeconfig.py tests/test_remote_sandbox_backend.py tests/test_aio_sandbox_provider.py -q
uv --directory backend run ruff check ../docker/provisioner/app.py tests/test_provisioner_pvc_volumes.py
```

Results:

```text
3 passed, 1 warning in 0.31s
1 passed, 1 warning in 0.31s
79 passed, 1 warning in 0.61s
All checks passed!
```

## Conclusion

The repo-owned provisioner code now consumes the same writable skills-path contract the gateway emits. The only remaining proof is environment-level: a real provisioner/K8s smoke.
