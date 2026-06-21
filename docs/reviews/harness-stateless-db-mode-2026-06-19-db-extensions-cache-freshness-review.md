# DB Extensions Cache Freshness Review

Date: 2026-06-19

Reviewed change set:

- DB-mode `get_extensions_config()` revision tracking
- `_extensions_revision` singleton state
- extensions cache freshness regression test

## Findings

### P1 Fixed: DB extensions singleton no longer stays stale after DB revision changes

`get_extensions_config()` now tracks the cached DB revision. In DB mode it loads the current revision and replaces the process-local config when the DB row changes.

Coverage added:

- Warm the singleton cache from revision 1.
- Save revision 2 through a separate store path.
- Call `get_extensions_config()` again.
- Assert revision 2 payload is returned.

### P1: MCP tools cache still needs revision-aware invalidation

The extensions config singleton now refreshes. However, MCP tool discovery and session caches can still hold tool lists created from an older config until reset.

Required follow-up:

- Store the extensions revision alongside MCP tools cache entries.
- Rebuild MCP tools when the active revision differs from the cached revision.
- Add tests that an MCP server enabled/disabled in DB becomes visible without manual reset in another process.

### P1: Skill prompt cache still needs revision-aware invalidation

Skill enabled state reads the refreshed config, but prompt cache freshness across processes still depends on local refresh calls.

Required follow-up:

- Track extensions revision in skill prompt cache.
- Refresh prompt content when skill enabled state revision changes.

## Positive Checks

- File-mode behavior remains unchanged.
- `reload_extensions_config()`, `reset_extensions_config()`, and `set_extensions_config()` keep revision state consistent.
- The cache freshness test fails before implementation and passes after it.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_config_secrets.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py tests/test_skills_custom_router.py tests/test_skills_loader.py tests/test_app_config_reload.py -q
```

Result:

```text
124 passed, 2 warnings in 1.05s
```

## Review Conclusion

The DB extensions config singleton is now revision-aware and can refresh after cross-process writes. The remaining cache work lives in derived caches built from extensions config: MCP tools/sessions and skill prompt cache.
