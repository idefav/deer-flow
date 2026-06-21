# DB Memory Selection Review

Date: 2026-06-19

Reviewed change set:

- DB-mode default memory storage selection
- SQLite parent directory creation in `DbMemoryStorage`
- memory regression tests

## Findings

### P1: DB memory cache can be stale across processes

`DbMemoryStorage.load()` returns the in-process cached memory row unless `reload()` is called. Another process can update the same memory row while this process keeps stale data.

Required follow-up:

- Store revision in cache and check the current DB revision before returning cached data.
- Or add bounded TTL / explicit invalidation on write events.

### P1: Concurrent memory writes are last-write-wins

The store increments revision but does not protect against concurrent updates based on an expected revision.

Required follow-up:

- Add compare-and-swap save semantics for memory rows.
- Use the CAS path from memory updater after it reloads and merges facts.

### P2: File-to-DB migration is still missing

DB mode now selects DB storage, but existing `memory.json` files are not imported.

Required follow-up:

- Add idempotent migration for global, per-user, and per-agent memory files.

## Positive Checks

- DB mode now defaults to `DbMemoryStorage`.
- File-mode memory tests remain green.
- SQLite parent directory creation is consistent with other sync DB stores.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py tests/test_memory_storage.py tests/test_memory_storage_user_isolation.py tests/test_memory_updater.py tests/test_memory_updater_user_isolation.py tests/test_memory_prompt_injection.py -q
uv --directory backend run ruff check tests/test_db_memory_storage.py packages/harness/deerflow/agents/memory/storage.py
```

Result:

```text
106 passed, 1 warning in 0.46s
All checks passed!
```

## Review Conclusion

This batch connects DB-backed memory storage to the default DB-mode runtime path. It does not yet solve cross-process freshness, concurrent write safety, or migration from existing memory files.
