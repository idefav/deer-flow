# MCP Legacy Aggregate Migration Review

Date: 2026-06-19

## Scope

- `DbExtensionsConfigStore.load_extensions_config()`
- `DbMcpServerStore.save_mcp_servers_in_session()`
- legacy `runtime_configs.extensions.payload_json.mcpServers`
- `tests/test_extensions_config_sources.py`
- implementation log Batch 49

## Findings

### P1 Fixed: Legacy aggregate MCP payloads are now materialized

Old DB rows can still contain MCP server definitions under `runtime_configs.extensions.payload_json.mcpServers`. Before this batch, DB mode could read that fallback shape, but it did not create first-class `mcp_servers` rows.

`DbExtensionsConfigStore.load_extensions_config()` now detects that state when:

- the dedicated `mcp_servers` table is empty.
- the aggregate extensions payload has a non-empty mapping under `mcpServers`.

It then writes those MCP definitions into `mcp_servers`, removes `mcpServers` from the aggregate runtime payload, increments the aggregate revision, and refreshes the aggregate content hash.

### P1 Fixed: Existing facade contract is preserved during migration

The caller still receives a normal `ExtensionsConfig` view with:

- MCP servers under `config.mcp_servers`.
- skill state from the aggregate runtime payload.
- extra extension fields such as `mcpInterceptors`.

The migration is therefore transparent to runtime callers that already load extensions through the facade.

### P2 Fixed: MCP row writes can share the caller transaction

`DbMcpServerStore.save_mcp_servers_in_session()` reuses the existing MCP save semantics inside a supplied SQLAlchemy session. This avoids duplicating row normalization and conflict logic for the load-time migration path.

### P2 Remaining: Direct MCP-store writes still bypass aggregate revision

The migration increments `runtime_configs.extensions.revision` because it changes the aggregate row. Direct calls to `DbMcpServerStore.save_mcp_servers()` still update only `mcp_servers`.

Risk:

- any cache keyed only by the aggregate runtime row revision could miss direct MCP-store writes.

Current constraint:

- direct MCP-store writes should remain internal unless a dedicated aggregate revision/meta row is introduced.

### P3 Remaining: Operator-visible dry run is still separate

This batch performs opportunistic compatibility migration during DB extensions load. That is useful for stateless runtime compatibility, but it does not give operators a standalone dry-run report before the first DB-mode load mutates old rows.

Potential follow-up:

- add an explicit migration/report command for legacy aggregate MCP payloads if production rollout requires preflight visibility.

## Verification

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py::test_db_extensions_config_store_migrates_legacy_aggregate_mcp_servers -q
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/mcp_store.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py
```

Results:

- targeted legacy migration test: `1 passed, 1 warning`
- related regression set: `29 passed, 1 warning`
- ruff: `All checks passed`

## Conclusion

The main MCP migration gap from the earlier MCP DB integration review is closed for runtime DB mode: legacy aggregate MCP definitions are converted to first-class `mcp_servers` rows while preserving the existing extensions facade. The remaining MCP hardening item is an explicit aggregate revision contract for direct MCP-table writes.
