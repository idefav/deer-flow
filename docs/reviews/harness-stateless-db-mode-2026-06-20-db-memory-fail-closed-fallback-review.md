# DB Memory Fail-closed Fallback Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/agents/memory/storage.py`
- `backend/tests/test_db_memory_storage.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: DB mode no longer silently falls back to file-backed Memory

`get_memory_storage()` previously reused the file-mode fallback for every storage initialization error. In DB/stateless mode that meant a missing preloaded DB config or broken DB memory storage could return `FileMemoryStorage`, causing later Memory writes to land on local disk. Batch 82 makes DB mode fail closed: the original initialization error is re-raised and the memory storage singleton remains unset.

### P1 Preserved: File mode fallback remains backward-compatible

The existing file-mode fallback behavior is still covered by `tests/test_memory_storage.py::TestGetMemoryStorage`. Invalid or unavailable custom storage still falls back to `FileMemoryStorage` when `DEER_FLOW_CONFIG_SOURCE` is not `db`.

### P2 Remaining: External live gates are still required

This batch closes a local Memory persistence escape hatch. It does not execute the remote provisioner/K8s smoke or real model live gates.

## Verification

Red check:

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py::test_get_memory_storage_db_mode_does_not_fall_back_to_file_storage -q
```

Result:

```text
1 failed, 1 warning in 0.27s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py -q
uv --directory backend run pytest tests/test_memory_storage.py::TestGetMemoryStorage -q
uv --directory backend run ruff check packages/harness/deerflow/agents/memory/storage.py tests/test_db_memory_storage.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
10 passed, 1 warning in 0.32s
6 passed, 1 warning in 0.25s
All checks passed!
4801 passed, 36 skipped, 12 warnings in 84.25s
All checks passed!
git diff --check produced no output.
```

## Conclusion

DB/stateless mode now treats Memory storage initialization failure as fatal instead of degrading to local file persistence. This better matches the stateless objective that Memory records must remain DB-backed in DB mode.
