# DB MemoryStorage Review

Date: 2026-06-19

Reviewed change set:

- `MemoryRow`
- `DbMemoryStorage`
- memory model registration
- memory DB focused tests
- implementation log Batch 7 entry

## Findings

### P1: Concurrent save conflict handling is not implemented yet

`DbMemoryStorage.save()` increments revision but does not perform compare-and-swap with an expected revision. This matches the current file-storage overwrite behavior but does not yet satisfy the full technical design's conflict handling.

Required follow-up:

- Add expected-revision based update.
- On conflict, reload latest memory and replay updater output once.
- Log and skip on repeated conflict rather than blind overwrite.

### P1: Sync DB engine is a deliberate V1 decision

The storage uses a synchronous SQLAlchemy engine so the existing synchronous `MemoryStorage` API does not run async SQLAlchemy through event-loop bridges.

Required follow-up:

- Document that this creates a separate DB engine/pool from the async app persistence engine.
- Add lifecycle handling if long-lived deployments need explicit disposal.
- Verify Postgres extra installs `psycopg` in production DB mode.

### P2: Legacy `user_id=None` is encoded as empty owner id

To preserve legacy calls without user id, `DbMemoryStorage` stores `owner_user_id=""`. Normal user-scoped memory uses the actual user id.

Required follow-up:

- Make migration tooling report legacy global memory separately.
- Decide whether legacy DB rows should remain supported after stateless migration completes.

### P2: Memory remains whole JSON blob

This batch intentionally does not split facts or add query recall.

Required follow-up:

- Add facts table only in the later recall enhancement phase.
- Keep `format_memory_for_injection()` and `max_injection_tokens` as the Phase 1 context-size guard.

## Positive Checks

- DB memory keeps the existing user/global and user+agent scope model.
- `save()` does not mutate caller dictionaries.
- `reload()` bypasses the local cache.
- Existing file memory, updater, and prompt injection tests continue to pass.
- `get_memory_storage()` can instantiate `DbMemoryStorage` by config path.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py tests/test_memory_storage.py tests/test_memory_storage_user_isolation.py tests/test_memory_updater.py tests/test_memory_updater_user_isolation.py tests/test_memory_prompt_injection.py -q
uv --directory backend run ruff check tests/test_db_memory_storage.py packages/harness/deerflow/agents/memory/storage.py packages/harness/deerflow/agents/memory/__init__.py packages/harness/deerflow/persistence/memory packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
104 passed, 1 warning in 4.78s
All checks passed!
```

## Review Conclusion

This batch implements Phase 1 DB memory persistence without changing recall or injection behavior. Conflict handling and migration remain required before the full memory portion of stateless DB mode is complete.
