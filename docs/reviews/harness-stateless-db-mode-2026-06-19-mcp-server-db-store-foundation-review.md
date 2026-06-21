# MCP Server DB Store Foundation Review

Date: 2026-06-19

Reviewed change set:

- `mcp_servers` ORM model
- `DbMcpServerStore`
- MCP DB store tests
- ORM model registration

## Findings

### P1 Fixed: MCP servers can now be represented as first-class DB rows

The implementation introduces an independent `mcp_servers` table and a store that can load those rows back into the existing `ExtensionsConfig` view. This is the first concrete step away from treating MCP server configuration as only a nested blob under `runtime_configs.extensions`.

Coverage added:

- store raw MCP env/header/OAuth secret payloads.
- preserve args, transport, description, and extra fields.
- expose a revisioned `ExtensionsConfig` view.

### P1 Fixed: Full-save stale writes can be rejected

`DbMcpServerStore.save_mcp_servers()` accepts expected revision and content hash. If another writer changes the MCP rows before save, the store raises `McpServerConfigConflictError`.

This closes the foundation-level race for the store itself. The router still needs to use this store before the API gets this protection from the first-class MCP table.

### P1: Runtime and API paths are not wired to the new store yet

DB-mode MCP API updates still write through `DbExtensionsConfigStore`, which persists MCP servers inside `runtime_configs.extensions`. The new MCP store is present and tested, but not yet the source of truth for runtime MCP readers.

Required follow-up:

- Compose `ExtensionsConfig` from `DbMcpServerStore` plus DB skill state.
- Update `/api/mcp/config` DB mode to read/write the MCP store.
- Keep legacy aggregate payload fallback during migration.

### P2: Aggregate revision is not a dedicated monotonic row yet

The store exposes a revisioned view using row revisions plus a stable content hash. This is enough for conflict checks when callers pass both revision and hash, but long-term cache invalidation would be cleaner with a dedicated monotonic aggregate revision row.

Required follow-up:

- Add an MCP config metadata row or revision table if runtime cache invalidation depends on a single integer revision.

## Positive Checks

- Secret fields are stored raw in DB and not passed through the API masking helper at the store boundary.
- Extra MCP server fields remain forward-compatible through `extra_json`.
- Existing MCP config, cache revision, and extensions source tests remain green.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_mcp_db_store.py tests/test_mcp_config_secrets.py tests/test_mcp_cache_revision.py tests/test_extensions_config_sources.py -q
```

Result:

```text
38 passed, 1 warning in 0.68s
```

```bash
uv --directory backend run ruff check tests/test_mcp_db_store.py packages/harness/deerflow/config/mcp_store.py packages/harness/deerflow/persistence/mcp/model.py packages/harness/deerflow/persistence/models/__init__.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

## Review Conclusion

The first-class MCP DB storage foundation is in place and tested. The next implementation step is source-of-truth wiring: DB-mode MCP runtime and API paths should read and write `mcp_servers`, with legacy aggregate payloads used only for fallback or migration.
