# Harness Stateless DB Mode Review: Remote Live Env Shape Preflight

Date: 2026-06-20

## Scope

Review Batch 102 changes to the live-gate preflight CLI:

- Validate `DEER_FLOW_REMOTE_AIO_PROVISIONER_URL` shape before reporting `remote_live` ready.
- Validate remote skills host path or host-path prefix shape before pytest invocation.
- Validate optional skills container path and ready timeout.
- Preserve the existing `missing_env` / `invalid_env` / `config_issues` JSON contract.

## Findings

### Closed: malformed remote-live env no longer reports ready

Before this batch, `remote_live` readiness only checked whether required env vars were present. A non-URL provisioner value, relative host path prefix, relative container path, or invalid timeout could still produce `ready_to_invoke: true`, leaving the failure to the live pytest process.

Batch 102 adds shape validation and reports those cases through `invalid_env`:

- `DEER_FLOW_REMOTE_AIO_PROVISIONER_URL`: must be an `http://` or `https://` URL with host.
- `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH` / `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX`: selected host path must be absolute.
- `DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH`: when supplied, must be absolute.
- `DEER_FLOW_REMOTE_AIO_READY_TIMEOUT`: when supplied, must be a positive integer.

Evidence:

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`

## Residual Risks

- The preflight still does not call the provisioner or inspect Kubernetes node paths. It can prove command-shape readiness, not provisioner reachability or hostPath/PVC permissions.
- The live remote smoke remains the production rollout gate for actual AIO file API write access.

## Verification

Focused checks run:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
15 passed, 1 warning
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
4835 passed, 36 skipped, 12 warnings in 86.08s
All checks passed!
git diff --check produced no output.
```
