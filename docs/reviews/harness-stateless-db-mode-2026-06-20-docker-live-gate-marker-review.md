# Docker Live Gate Marker Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/pyproject.toml`
- `backend/tests/test_sandbox_orphan_reconciliation_e2e.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

## Findings

### P1 Fixed: Docker-backed E2E tests are now part of the live-gate surface

`test_sandbox_orphan_reconciliation_e2e.py` requires a local Docker daemon but was invisible to `pytest -m live`. Batch 77 registers a `docker_live` marker and marks that module as both `live` and `docker_live`, so operators can run all live gates or just local Docker gates explicitly.

### P1 Verified: Local Docker lifecycle E2E runs on this workstation

Unlike remote provisioner/K8s or model-backed gates, the local Docker daemon is available in this environment. The marked Docker gate was executed and passed all three tests.

### P2 Remaining: Docker gate does not replace remote provisioner validation

The Docker lifecycle E2E verifies local container discovery and cleanup. It does not prove K8s hostPath/PVC permissions or remote AIO file API writeability, so the remote provisioner/K8s smoke remains a separate rollout gate.

## Verification

Red collection check before markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_sandbox_orphan_reconciliation_e2e.py -q
```

Result:

```text
no tests collected (3 deselected) in 0.09s
exit code 5
```

Green collection and execution:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest --collect-only -m docker_live tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest -m docker_live tests/test_sandbox_orphan_reconciliation_e2e.py -q
```

Result:

```text
3 tests collected in 0.10s
3 tests collected in 0.09s
3 passed, 1 warning in 9.83s
```

Marked live-gate aggregate after adding `docker_live`:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest --collect-only -m docker_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py tests/test_sandbox_orphan_reconciliation_e2e.py -q
```

Result:

```text
39/71 tests collected (32 deselected) in 0.47s
3/71 tests collected (68 deselected) in 0.44s
3 passed, 36 skipped, 32 deselected, 1 warning in 10.03s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4792 passed, 36 skipped, 12 warnings in 84.05s
All checks passed!
git diff --check produced no output.
```

## Conclusion

The local Docker external gate is now discoverable through standard pytest marker selection and has fresh passing evidence in this workstation. The remaining unexecuted external gates are remote provisioner/K8s and real model credentials.
