# Live Gate Marker Regression Guard Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/tests/test_live_gate_markers.py`
- `backend/tests/test_client_e2e.py`
- `backend/tests/test_deferred_tool_promotion_real_llm.py`
- `backend/tests/test_aio_sandbox_live.py`
- `backend/tests/test_aio_sandbox_remote_live.py`
- `backend/tests/test_client_live.py`
- `backend/tests/test_create_deerflow_agent_live.py`
- `backend/tests/test_sandbox_orphan_reconciliation_e2e.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: Live-gate marker coverage now has a regression test

Batches 75-77 manually marked existing live gates, but the contract was easy to regress when adding a new opt-in test. Batch 78 adds `test_live_gate_markers.py`, which scans test sources for known external-dependency signals and fails when the required pytest markers are missing.

### P1 Verified: The detector catches missing remote live markers

The new detector was added with a red fixture that contains `DEER_FLOW_RUN_REMOTE_AIO_SANDBOX` but no `live` / `remote_live` marker. The first run failed with the expected missing-marker message, then passed after implementing AST-based marker extraction and the live-gate rules.

### P2 Guardrail Scope: Fake-LLM E2E tests stay in the normal suite

The guard only keys off explicit live opt-in signals such as `ONEAPI_E2E`, `DEER_FLOW_RUN_*`, real-LLM helper usage, `_live.py` credential patterns, and Docker daemon requirements. It intentionally does not require `live` markers for fake-LLM HTTP/runtime E2E tests that use placeholder `OPENAI_API_KEY` values but do not call external services.

### P2 Remaining: The guard does not execute external services

This meta-test proves marker discoverability, not remote cluster permissions or real model behavior. The remote provisioner/K8s and real model live executions remain rollout gates.

## Verification

Red detector check:

```bash
uv --directory backend run pytest tests/test_live_gate_markers.py -q
```

Result:

```text
1 failed, 1 passed, 1 warning in 0.19s
```

Expected failure:

```text
assert [] == ['test_remote_live_missing.py requires live, remote_live markers']
```

Green detector check:

```bash
uv --directory backend run pytest tests/test_live_gate_markers.py -q
```

Result:

```text
2 passed, 1 warning in 0.18s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4794 passed, 36 skipped, 12 warnings in 84.01s
All checks passed!
git diff --check produced no output.
```

## Conclusion

The live-gate marker surface now has an automated regression guard. This reduces the risk that future real-LLM, remote sandbox, or Docker E2E tests are added without discoverable `live` / specialized marker coverage, while preserving normal fake-service E2E coverage in the default suite.
