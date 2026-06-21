# Memory Updater Stale-save Retry Review

Date: 2026-06-19

Reviewed change set:

- `MemoryUpdater._finalize_update()` retry behavior
- memory updater stale-save regression test
- cache-isolation test update for retry semantics

## Findings

### P1 Fixed: Updater no longer silently drops storage-level stale-save failures

After Batch 19, `DbMemoryStorage.save()` can return `False` when the caller is saving against a stale revision. `_finalize_update()` now treats a failed save as recoverable once: it reloads the latest memory, reapplies the same parsed update data, strips upload mentions again, and retries one save.

Coverage added:

- First save returns `False`.
- Updater reloads latest memory.
- Retry payload preserves the concurrently saved fact and adds the current update fact.
- Second save returns `True`.

### P2: Failure reason is still not typed

The updater retries on any `False` save result. That covers stale-save conflicts but also retries ordinary storage failures once.

Required follow-up:

- Introduce typed storage save results or exceptions for conflict vs I/O failure.
- Emit telemetry for conflict retries and retry exhaustion.

### P2: Retry count is intentionally bounded to one

One retry avoids unbounded loops and duplicate model calls. High-contention environments may still fail after a second concurrent write.

Required follow-up:

- Consider configurable retry count with jitter/backoff.
- Add integration coverage with real `DbMemoryStorage` revision conflicts.

## Positive Checks

- The model response is parsed once and reused; no second LLM call is introduced.
- Retry reapplies normalized update data to the latest memory snapshot.
- Existing cache mutation guard still holds when both saves fail.
- User and agent scope are passed through to both `save()` and `reload()`.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_memory_updater.py -q
```

Result:

```text
54 passed, 1 warning in 0.31s
```

## Review Conclusion

The updater now has a bounded recovery path for DB memory revision conflicts. The remaining design gap is observability and typed failure semantics, so operational metrics can tell stale conflicts apart from persistent storage errors.
