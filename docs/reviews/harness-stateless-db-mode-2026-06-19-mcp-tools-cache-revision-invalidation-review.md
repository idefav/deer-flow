# MCP Tools Cache Revision Invalidation Review

Date: 2026-06-19

Reviewed change set:

- `get_extensions_config_revision()`
- MCP tools cache `_config_revision`
- DB revision stale detection in `_is_cache_stale()`
- MCP cache revision regression test

## Findings

### P1 Fixed: MCP tools cache can now detect DB extensions revision changes

MCP cache stale detection no longer depends only on file mtime. In DB mode, the cache records the extensions revision at initialization and treats later revision changes as stale.

Coverage added:

- Cache initialized against revision 1.
- DB extensions row advanced to revision 2.
- `_is_cache_stale()` returns `True`.

### P2: End-to-end lazy reload coverage is still missing

The stale predicate is covered directly. A full test for `get_cached_mcp_tools()` resetting and reinitializing after revision change is still useful, but requires mocking the async initialization boundary carefully.

Required follow-up:

- Patch `initialize_mcp_tools()` or `get_mcp_tools()` in a focused test.
- Assert `get_cached_mcp_tools()` calls `reset_mcp_tools_cache()` and returns tools from the new revision.

### P2: Revision checks add DB reads on cache access

`_is_cache_stale()` now checks the current DB revision in DB mode. That is correct for freshness, but may become hot if MCP tools are read frequently.

Required follow-up:

- Consider a short TTL around revision checks.
- Or move to pub/sub/event invalidation when multi-process deployments mature.

## Positive Checks

- File-mode mtime invalidation remains intact.
- Reset clears both mtime and DB revision state.
- MCP session pool reset behavior remains unchanged.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py tests/test_mcp_config_secrets.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py tests/test_extensions_config_sources.py -q
```

Result:

```text
98 passed, 1 warning in 0.87s
```

## Review Conclusion

MCP tools cache now has DB-mode stale detection keyed by extensions revision. The remaining work is higher-level coverage and performance tuning, not the core ability to notice cross-process DB config changes.
