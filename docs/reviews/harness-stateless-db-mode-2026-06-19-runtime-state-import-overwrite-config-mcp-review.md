# Runtime State Import Overwrite Config/MCP Review

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- config/extensions/MCP overwrite behavior
- runtime-state import overwrite tests
- implementation log Batch 42

## Findings

### P2 Fixed: MCP server overwrite is now visible in preflight accounting

`DbExtensionsConfigStore` stores MCP servers as first-class `mcp_servers` rows, but overwrite preflight previously counted only the aggregate `runtime_configs.extensions` conflict. A replacement run that changed both skill state and an MCP server therefore reported two conflicts instead of three.

The import preflight now inspects `mcp_servers` and reports an `mcp_server` conflict for each incoming server name that already exists in DB.

### P2 Fixed: Config/extensions/MCP replacement path has direct regression coverage

The new test seeds old app config, extensions skill state, and MCP server config, then imports replacement files with `overwrite=True`.

Coverage asserts:

- `runtime_configs.app` receives the replacement app config.
- `runtime_configs.extensions` receives the replacement skill state.
- `mcp_servers.github` receives the replacement command and env refs.
- `applied.overwritten_by_resource` reports `app_config`, `extensions_config`, and `mcp_server`.

### P2: Aggregate extensions and MCP row conflicts are intentionally both counted

The source file is one `extensions_config.json`, but DB storage has two affected resources:

- `runtime_configs.extensions` for skill state and non-MCP extension fields.
- `mcp_servers` for first-class MCP server definitions.

Counting both makes overwrite reports match the actual DB rows being replaced. Operators should treat the count as "DB resource conflicts" rather than "source files overwritten".

### P3: Apply summary is still preflight-conflict based

`overwritten_by_resource` counts rows that existed before apply. It does not yet compare hashes before and after apply, so a same-content re-import can still be counted as overwritten if the row existed.

Recommendation:

- Keep current behavior for conservative migration audit logs.
- Add optional changed-row accounting later if operators need a stricter "content changed" metric.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Results:

- targeted overwrite config/MCP test: `1 passed, 1 warning`
- related regression set: `22 passed, 1 warning`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

The previously open overwrite coverage gap for app config and extensions/MCP is closed. The migration command now reports replacement conflicts for the aggregate config rows and the dedicated MCP server rows that will be touched by an overwrite import.
