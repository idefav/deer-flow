# MCP Direct Write Revision Review

Date: 2026-06-19

## Scope

- `DbMcpServerStore.save_mcp_servers()`
- `DbMcpServerStore.save_mcp_servers_in_session()`
- `DbExtensionsConfigStore.save_extensions_config()`
- DB-mode `get_extensions_config()` singleton refresh
- implementation log Batch 50

## Findings

### P2 Fixed: Direct MCP-store writes now invalidate DB extensions cache

Before this batch, a direct call to `DbMcpServerStore.save_mcp_servers()` updated `mcp_servers` but did not change `runtime_configs.extensions.revision`. DB-mode `get_extensions_config()` refreshes its singleton by comparing that aggregate revision, so the process could keep returning stale MCP servers.

Direct MCP writes now bump or create the aggregate extensions runtime row in the same transaction as the MCP row save. This gives the existing DB extensions singleton and MCP tool cache a revision change they can observe.

### P2 Fixed: Facade writes remain single-revision operations

`DbExtensionsConfigStore.save_extensions_config()` now writes MCP rows through `DbMcpServerStore.save_mcp_servers_in_session()` instead of opening a separate MCP-store transaction.

This matters because:

- facade writes keep their existing `expected_revision` check against `runtime_configs.extensions`.
- first-time facade writes do not race with a direct MCP-store-created aggregate row.
- the aggregate revision increments once for the facade save.

### P2 Fixed: Legacy aggregate MCP fields are stripped during direct bump

When direct MCP writes bump the aggregate row, the helper removes legacy `mcpServers` from `runtime_configs.extensions.payload_json` and keeps `skills` present. This prevents old embedded MCP payloads from surviving as a second source of truth after direct MCP row replacement.

### P3 Remaining: Direct MCP writes are full replacements

`DbMcpServerStore.save_mcp_servers()` still uses full-set replacement semantics:

- missing incoming names are deleted.
- existing names are updated when content changes.
- stale conflict checks are based on the MCP row aggregate revision/hash.

That is acceptable for current internal use, but any future public MCP API should expose explicit partial upsert/delete semantics instead of relying on full replacement.

## Verification

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py::test_db_extensions_config_cache_refreshes_after_direct_mcp_store_write -q
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_cache_revision.py tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/mcp_store.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_cache_revision.py
```

Results:

- targeted direct-write cache test: `1 passed, 1 warning`
- related regression set: `31 passed, 1 warning`
- ruff: `All checks passed`

## Conclusion

The MCP direct-write cache invalidation gap is closed for the current DB-mode runtime. Both facade writes and direct MCP-store writes now leave `runtime_configs.extensions.revision` in a state that existing extension and MCP tool caches can observe.
