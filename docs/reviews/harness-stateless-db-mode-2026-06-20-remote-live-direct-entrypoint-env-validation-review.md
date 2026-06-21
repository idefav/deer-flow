# Harness Stateless DB Mode Review: Remote Live Direct Entrypoint Env Validation

Date: 2026-06-20

## Scope

Review Batch 103 changes to the direct remote provisioner/K8s live smoke entrypoint:

- Reuse the live-gate preflight env-shape validation inside `tests/test_aio_sandbox_remote_live.py`.
- Fail early with an actionable message when malformed remote-live env is supplied.
- Preserve the existing missing-env skip behavior for unconfigured developer workstations.

## Findings

### Closed: direct pytest execution no longer bypasses env-shape validation

Batch 102 made `scripts/check_stateless_live_gates.py --gate remote_live` reject malformed remote-live env values, but direct execution of `tests/test_aio_sandbox_remote_live.py` still reached later code paths before surfacing invalid values.

Batch 103 adds `_invalid_remote_live_env_message()` to the remote live smoke module and invokes it immediately after the explicit opt-in check. Invalid values now fail before `RemoteSandboxBackend` is constructed or timeout parsing happens.

Evidence:

- `backend/tests/test_aio_sandbox_remote_live.py`
- `backend/tests/test_stateless_live_gate_check.py::test_remote_live_pytest_entrypoint_reports_invalid_env_before_backend_create`

## Residual Risks

- This still validates only env shape. It does not prove provisioner reachability, Kubernetes hostPath/PVC existence, or AIO file API write permissions.
- The remote live smoke must still run in the target provisioner/K8s environment before production rollout.

## Verification

Focused checks run:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check tests/test_aio_sandbox_remote_live.py tests/test_stateless_live_gate_check.py
```

Result:

```text
16 passed, 1 warning
1 skipped, 1 warning
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4836 passed, 36 skipped, 12 warnings in 86.09s
All checks passed!
git diff --check produced no output.
```
