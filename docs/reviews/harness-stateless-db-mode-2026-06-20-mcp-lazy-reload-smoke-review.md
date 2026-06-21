# MCP Lazy Reload Smoke Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/mcp/cache.py`
- `backend/tests/test_mcp_cache_revision.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

## Findings

### P1 Fixed: MCP cache reload is now covered at the public entrypoint

Earlier coverage proved `_is_cache_stale()` notices DB-backed extensions revision changes. Batch 73 adds a higher-level smoke around `get_cached_mcp_tools()` so the actual runtime entrypoint is covered: after DB MCP config advances from one revision to another, the cached tools are reset and rediscovered for the new revision.

### P2 Fixed: No-current-loop lazy initialization no longer uses deprecated event-loop lookup

The new smoke exposed a Python 3.12 deprecation warning from `asyncio.get_event_loop()` when no event loop exists in the current thread. The lazy initialization branch now uses `asyncio.get_running_loop()` to distinguish running-loop contexts from synchronous no-loop contexts, then uses `asyncio.run()` for the latter.

### P2 Remaining: Cross-process cache propagation still depends on revision polling on access

This implementation is access-triggered. Other gateway processes observe committed DB revisions when they next call the cache entrypoint; there is no push notification or pub/sub invalidation. That is acceptable for V1, but it should remain explicit if MCP tools require near-real-time multi-process propagation later.

## Verification

Command run:

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py -q
```

Result:

```text
2 passed, 1 warning in 0.42s
```

The remaining warning is an unrelated LangChain pending deprecation from the test environment.

## Conclusion

MCP DB-mode cache invalidation now has both helper-level and public-entrypoint coverage. The remaining risk is operational latency across processes, not correctness inside a process once the cache path is called.
