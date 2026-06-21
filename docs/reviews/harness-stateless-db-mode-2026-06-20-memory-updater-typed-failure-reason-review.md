# Memory Updater Typed Failure Reason Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/agents/memory/updater.py`
- `backend/packages/harness/deerflow/agents/memory/__init__.py`
- `backend/tests/test_memory_updater.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P2 Fixed: Save retry exhaustion is now typed

The Memory updater already handled stale DB-memory saves by reloading latest memory and retrying once. If both saves failed, callers only received `False`. Batch 85 adds `MemoryUpdateFailureReason.SAVE_RETRY_EXHAUSTED` and exposes it through `MemoryUpdater.last_failure_reason`, preserving the existing boolean API while making this failure mode distinguishable.

### P2 Guarded: Failure reason does not leak across successful updates

The new coverage verifies that a later successful `_finalize_update()` clears an earlier `SAVE_RETRY_EXHAUSTED` reason. This avoids stale diagnostic state on long-lived `MemoryUpdater` instances.

### P2 Preserved: Existing Memory updater behavior remains compatible

`update_memory()` and `aupdate_memory()` still return `bool`; no caller contract was changed. The new typed reason is an optional side-channel for operators/tests that need to distinguish storage retry exhaustion from parse, skip, or model failures.

### P2 Remaining: External live gates are still required

This batch closes a local Memory observability gap. It does not execute the remote provisioner/K8s smoke or real model live gates.

## Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_memory_updater.py::TestFinalizeCacheIsolation::test_finalize_records_typed_reason_when_retry_save_fails -q
uv --directory backend run pytest tests/test_memory_updater.py::TestFinalizeCacheIsolation::test_finalize_records_typed_reason_when_retry_save_fails tests/test_memory_updater.py::TestFinalizeCacheIsolation::test_finalize_clears_previous_failure_reason_after_success -q
```

Result:

```text
1 error, 1 warning in 0.30s
1 failed, 1 passed, 1 warning in 0.29s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_memory_updater.py -q
uv --directory backend run ruff check packages/harness/deerflow/agents/memory/updater.py tests/test_memory_updater.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
57 passed, 1 warning in 0.29s
All checks passed!
4806 passed, 36 skipped, 12 warnings in 83.97s
All checks passed!
git diff --check produced no output.
```

## Conclusion

Memory updater stale-save retry exhaustion now has a typed diagnostic reason without changing the existing boolean update API.
