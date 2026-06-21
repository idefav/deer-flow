# Harness Stateless Object Storage Review: Provisioner Object Runtime Gate

Date: 2026-06-21

## Scope

This batch updates the Kubernetes sandbox provisioner so object runtime storage does not mount a runtime PVC or hostPath user-data directory into sandbox Pods.

## Findings

- `RUNTIME_STORAGE_BACKEND` now controls provisioner runtime file mode and defaults to `filesystem`.
- In `object` mode, the provisioner omits the `user-data` volume and `/mnt/user-data` volume mount.
- In `object` mode, `USERDATA_PVC_NAME` raises a runtime error instead of silently mounting the PVC.
- In `object` mode, request-scoped extra mounts under `/mnt/user-data` or `/mnt/acp-workspace` are rejected.
- The mount contract hash now includes `runtime_storage_backend`, preventing unsafe reuse of existing Pods created under a different runtime storage mode.

## Verification

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py -q
uv --directory backend run ruff check ../docker/provisioner/app.py tests/test_provisioner_pvc_volumes.py
git diff --check
```

Result:

```text
31 passed, 1 warning in 0.43s
All checks passed!
git diff --check produced no output.
```

## Remaining Risks

- Compose and runbook examples still need to expose `RUNTIME_STORAGE_BACKEND=object`.
- Object-mode sandbox creation still uses legacy lock-file paths.
- Full runtime PVC removal still needs live evidence in the target Kubernetes environment.
