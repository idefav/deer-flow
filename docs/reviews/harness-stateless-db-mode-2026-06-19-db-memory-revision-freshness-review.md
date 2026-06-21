# DB Memory Revision Freshness Review

Date: 2026-06-19

Reviewed change set:

- `DbMemoryStorage.load()` revision freshness check
- `DbMemoryStorage.save()` optimistic stale-write guard
- DB memory regression tests

## Findings

### P1 Fixed: DB memory cache no longer stays stale after another process updates the row

`DbMemoryStorage.load()` now checks the current DB row revision before returning cached memory. If another storage instance has advanced the revision, the row is reloaded and the local cache is updated.

Coverage added:

- One storage instance warms its cache.
- A second storage instance updates the same user memory row.
- The first storage instance calls `load()` and sees the updated summary.

### P1 Fixed: Stale cached saves no longer overwrite newer DB rows

`DbMemoryStorage.save()` now compares the cached expected revision with the DB row revision. If the DB row has changed since this instance loaded or saved the memory, the save returns `False` and does not overwrite the newer row.

Coverage added:

- Two storage instances load revision 1.
- The second instance saves revision 2.
- The first instance attempts to save its stale revision 1 payload.
- The stale save is rejected and revision 2 remains intact.

### P1: Memory updater retry/merge handling is still missing

The storage layer now detects stale writes, but callers still need a policy for `False` save results. For memory updates, the safer production behavior is reload, reapply/merge the proposed update, and retry with a bounded attempt count.

Required follow-up:

- Audit memory updater call sites for `save()` result handling.
- Add retry/merge behavior for stale DB memory saves.
- Add tests for conflict retry behavior at the updater level.

## Positive Checks

- Cached load remains fast when DB revision has not changed.
- Uncached saves retain current upsert behavior.
- Caller dictionaries are still deep-copied before persistence mutation.
- The new tests fail before the implementation and pass after it.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py -q
```

Result:

```text
9 passed, 1 warning in 0.28s
```

## Review Conclusion

The storage-level P1s for DB memory freshness and stale overwrite prevention are closed. The next risk is caller behavior: memory update workflows must treat a failed save as a conflict that requires reload and retry, not as an ordinary persistence failure.
