# MCP DB Store Integration Review

Date: 2026-06-19

Reviewed change set:

- `DbExtensionsConfigStore` load/save composition
- MCP server split from `runtime_configs.extensions`
- MCP dedicated row integration test

## Findings

### P1 Fixed: DB-mode MCP state is now written to `mcp_servers`

`DbExtensionsConfigStore.save_extensions_config()` now splits `mcpServers` out of the aggregate extensions payload and saves them through `DbMcpServerStore`. The runtime config row keeps skills and extra extension fields, but no longer stores MCP servers as the primary DB representation.

Coverage added:

- save config with MCP server, skill state, and `mcpInterceptors`.
- assert MCP headers are in the MCP table.
- assert `runtime_configs.extensions.payload_json` does not contain `mcpServers`.
- load through the normal extensions store and recover the same view.

### P1 Fixed: Existing API/runtime readers keep the same `ExtensionsConfig` contract

`DbExtensionsConfigStore.load_extensions_config()` now composes MCP rows with runtime-config skills and extra fields. This keeps callers such as MCP routes, tool cache, skill toggles, and prompt cache on the existing `ExtensionsConfig` shape while moving MCP persistence behind the facade.

### P1: Migration from legacy aggregate payload is still missing

The loader keeps a fallback for legacy `runtime_configs.extensions.mcpServers` when the MCP table is empty, so existing deployments can still read old rows. However, there is not yet an import/migration command that materializes those legacy MCP servers into `mcp_servers`.

Required follow-up:

- Add migration dry-run and apply path for legacy MCP aggregate payloads.
- Report secret/env-ref risks before applying.

### P2: Direct MCP-store writes bypass the aggregate runtime revision

When writes go through `DbExtensionsConfigStore`, the existing runtime row revision remains the aggregate cache invalidation boundary. Direct writes to `DbMcpServerStore` update MCP rows but do not bump `runtime_configs.extensions.revision`.

Required follow-up:

- Keep direct MCP store writes internal until a dedicated aggregate revision/meta row is introduced.
- Add a runtime cache test if direct MCP store writes become public.

## Positive Checks

- Existing DB-mode MCP API tests still pass through the facade.
- MCP secret masking/preservation behavior remains intact.
- Existing file-mode extension source behavior is unchanged.
- Legacy aggregate fallback is preserved in code.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_config_secrets.py tests/test_mcp_cache_revision.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py -q
```

Result:

```text
101 passed, 1 warning in 0.94s
```

```bash
uv --directory backend run ruff check tests/test_extensions_config_sources.py tests/test_mcp_db_store.py packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/mcp_store.py packages/harness/deerflow/persistence/mcp/model.py packages/harness/deerflow/persistence/models/__init__.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

## Review Conclusion

MCP DB storage is now integrated behind the existing extensions facade. The next MCP tasks are migration and hardening: move legacy aggregate payloads into the first-class table and add an explicit aggregate revision contract if direct MCP-table writes become part of the runtime API.
