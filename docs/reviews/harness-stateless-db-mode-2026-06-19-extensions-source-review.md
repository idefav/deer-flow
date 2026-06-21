# ExtensionsConfigSource Review

Date: 2026-06-19

Reviewed change set:

- `deerflow.config.extensions_sources`
- Gateway DB startup extensions loading
- extensions source tests
- DB startup extensions test
- implementation log Batch 6 entry

## Findings

### P1: This is an aggregate view, not the final MCP/skills schema

`DbExtensionsConfigSource` currently reads `runtime_configs.extensions`. That is useful for bootstrapping DB mode, but the technical design still calls for dedicated MCP and skills state storage.

Required follow-up:

- Add `mcp_servers` table and repository.
- Add skill enabled state table or integrate it with the future skills DB schema.
- Make `ExtensionsConfigSource` compose the final view from those tables.

### P1: Runtime call sites still need migration to the source facade

DB-mode startup now uses `DbExtensionsConfigSource`, but several runtime paths may still call `ExtensionsConfig.from_file()` directly.

Required follow-up:

- Audit MCP tools, ACP bridge, sandbox allowed paths, and skill enabled state reads.
- Replace runtime direct file reads with a single extensions facade.
- Keep `ExtensionsConfig.from_file()` for file mode, migration, and rollback only.

### P1: MCP operational risks are not solved by this batch

Loading extensions from DB does not yet handle MCP cache invalidation, OAuth secret storage, masked round-trips, stdio session pool state, or interceptor allowlists.

Required follow-up:

- Implement MCP DB store and API semantics as a dedicated batch.
- Add revision/hash reset behavior for MCP caches and pooled sessions.

## Positive Checks

- DB-mode startup can now load both app config and extensions config from the bootstrap DB.
- Missing DB extensions config preserves the existing optional behavior by returning an empty config.
- File extensions source exists for parity and future facade composition.
- Tests cover MCP server and skill enabled state payloads.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_gateway_db_config_startup.py -q
```

Result:

```text
5 passed, 1 warning in 0.55s
```

## Review Conclusion

This batch removes the immediate file dependency for extensions during DB-mode startup. It is a bridge toward the final design, not the final MCP/skills persistence model.
