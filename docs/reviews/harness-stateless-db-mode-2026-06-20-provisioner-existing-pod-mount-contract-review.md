# Provisioner Existing Pod Mount Contract Review

Date: 2026-06-20

## Scope

- `docker/provisioner/app.py`
- `tests/test_provisioner_pvc_volumes.py`
- remote provisioner/K8s DB-mode writable skills mount contract
- implementation log Batch 91

## Findings

### P1 Fixed: Existing sandbox Pods no longer bypass mount-contract validation

The bundled provisioner previously treated `POST /api/sandboxes` as idempotent by `sandbox_id` alone. If a Pod already had a NodePort Service, the provisioner returned it without checking whether its volume mounts matched the current request.

That is unsafe for DB/stateless mode because the gateway can request a writable `skills.container_path` extra mount for the same deterministic thread sandbox id. Reusing an older Pod without that mount would make sandbox creation appear successful, then fail later when runtime-context materialization tried to write prompt-visible skill files.

Batch 91 adds a stable mount-contract hash annotation to new Pods and checks the stored hash before returning an existing Pod.

### P1 Fixed: Mismatched existing Pods fail closed with HTTP 409

When an existing Pod has a different mount contract, the provisioner now rejects the create request with HTTP 409 and an actionable message to destroy the stale sandbox before retrying. Matching contracts remain idempotent and return the existing sandbox URL.

### P2 Remaining: Real cluster permissions still require live smoke

This batch prevents stale or incompatible Pod reuse in the bundled provisioner. It still does not prove that a deployed Kubernetes node, PVC, `hostPath`, or sandbox user can write to the requested mount. The remote live smoke remains the production rollout proof.

## Verification

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::test_create_sandbox_rejects_existing_pod_with_mismatched_mount_contract -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py tests/test_aio_sandbox_provider.py::test_remote_backend_create_forwards_extra_mounts -q
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check ../docker/provisioner/app.py tests/test_provisioner_pvc_volumes.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `1 failed, 1 warning`
- targeted mismatch guard: `1 passed, 1 warning`
- provisioner PVC/mount regression set: `26 passed, 1 warning`
- provisioner plus remote-backend focused set: `27 passed, 1 warning`
- remote live smoke default workstation behavior: `1 skipped, 1 warning`
- full backend suite: `4811 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

The bundled provisioner now treats the mount contract as part of sandbox identity for idempotent create requests. This moves one more DB/stateless failure mode from late runtime materialization into an early, explicit provisioner error.
