# DB Extensions Revision Guard Review

Date: 2026-06-19

Reviewed change set:

- `ExtensionsConfigConflictError`
- `DbExtensionsConfigStore.save_extensions_config(expected_revision=...)`
- MCP DB-mode config update conflict handling
- skill enabled DB-mode conflict handling
- extensions/MCP regression tests

## Findings

### P1 Fixed: Concurrent MCP/skill updates no longer silently overwrite each other

The shared `runtime_configs.extensions` payload now supports optimistic revision checks. Callers can load the current revision and save with `expected_revision`; if another writer advances the row first, the save raises `ExtensionsConfigConflictError`.

Coverage added:

- Store-level stale expected revision is rejected.
- The newer extensions payload remains intact after a stale save attempt.
- MCP DB-mode PUT maps revision conflicts to HTTP 409.

### P1 Fixed: MCP DB-mode PUT now uses the DB store as the revision source

The MCP router no longer reads only the process extensions cache before saving in DB mode. It loads the current DB revision through `DbExtensionsConfigStore`, performs secret-preserving merge, and saves with that revision.

### P2: Cross-process cache invalidation is still not automatic

This batch prevents stale writes. It does not make already-running processes automatically reload cached MCP tools or skill prompt state after another process commits a valid update.

Required follow-up:

- Track last-seen extensions revision in runtime caches.
- Reload extensions and reset MCP/skill prompt caches when revision changes.
- Add tests with two cache instances or simulated process-local caches.

### P2: Merge logic remains duplicated

MCP config PUT and skill enabled toggle both reconstruct the full extensions payload.

Required follow-up:

- Add a shared helper that loads revision, mutates only one section, preserves extras, and saves with CAS.
- Keep MCP secret preservation as a dedicated merge step inside that helper.

## Positive Checks

- Existing MCP secret preservation behavior is unchanged.
- Existing top-level extras like `mcpInterceptors` are still preserved.
- Skill enabled toggles now share the same optimistic revision protection.
- Conflict responses use HTTP 409, giving clients a clear reload-and-retry signal.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_config_secrets.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py tests/test_skills_custom_router.py -q
```

Result:

```text
106 passed, 2 warnings in 0.92s
```

## Review Conclusion

The DB extensions payload now has optimistic concurrency protection for MCP and skill writes. The next production risk is freshness: process-local caches still need revision-aware invalidation after another process successfully commits an update.
