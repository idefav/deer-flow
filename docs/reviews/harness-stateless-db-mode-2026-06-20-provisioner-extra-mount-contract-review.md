# Provisioner Extra Mount Contract Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/community/aio_sandbox/remote_backend.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: Remote provisioner payload dropped `extra_mounts`

`AioSandboxProvider` now computes a writable per-thread `skills.container_path` mount in DB mode, but `RemoteSandboxBackend._provisioner_create()` previously ignored the `extra_mounts` argument when posting to `/api/sandboxes`.

That meant local Docker AIO had a writable `/mnt/skills` path, while provisioner/K8s mode had no way to receive the same requirement from the gateway. Batch 70 serializes `extra_mounts` into the provisioner create payload:

```json
{
  "extra_mounts": [
    {
      "host_path": "/host/thread/skills",
      "container_path": "/mnt/skills",
      "read_only": false
    }
  ]
}
```

The existing no-mount payload remains unchanged when `extra_mounts` is absent.

### P2 Remaining: External provisioner must consume the contract

This change closes the gateway-side contract forwarding gap. It does not prove the external provisioner/K8s service creates a writable volume. The provisioner implementation must translate `extra_mounts` into an `emptyDir`, PVC subPath, or equivalent writable volume and verify the AIO shell/file API user can write to `container_path`.

## Verification

Commands run:

```bash
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_remote_backend_create_forwards_extra_mounts -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_remote_backend_create_forwards_effective_user_id -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py tests/test_sandbox_materializer.py tests/test_aio_sandbox.py tests/test_aio_sandbox_live.py -q
uv --directory backend run ruff check packages/harness/deerflow/community/aio_sandbox/remote_backend.py tests/test_aio_sandbox_provider.py
git diff --check
```

Results:

```text
1 passed, 1 warning in 0.17s
1 passed, 1 warning in 0.17s
68 passed, 1 skipped, 1 warning in 0.93s
All checks passed!
git diff --check produced no output.
```

## Conclusion

Gateway-side provisioner mount-contract forwarding is now covered. The remaining remote proof is outside this process: the provisioner/K8s deployment must consume `extra_mounts` and pass a remote live smoke.
