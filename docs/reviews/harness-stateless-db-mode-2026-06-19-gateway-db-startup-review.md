# Gateway DB Startup Preload Review

Date: 2026-06-19

Reviewed change set:

- bootstrap URL to `DatabaseConfig` parser
- Gateway `_load_startup_config()`
- `lifespan()` startup config load path
- `langgraph_runtime()` engine reuse guard
- DB startup focused tests
- implementation log Batch 5 entry

## Findings

### P1: Extensions are still file-backed during DB startup

DB-mode startup now reads `runtime_configs.app` from DB, but it still calls `ExtensionsConfig.from_file()` for MCP and skill enabled state.

Required follow-up:

- Implement `ExtensionsConfigSource`.
- Ensure DB mode startup loads extensions from DB once that source exists.
- Keep file-backed extensions only for file mode, migration, and rollback.

### P1: Engine ownership needs an explicit contract

`_load_startup_config()` initializes the persistence engine in DB mode, and `langgraph_runtime()` now skips initialization if an engine already exists. Shutdown still calls `close_engine()` from `langgraph_runtime()`.

Required follow-up:

- Document and test that `langgraph_runtime()` owns shutdown even when startup config preload initialized the engine.
- Add a full lifespan test to ensure the engine closes after DB-mode startup.

### P1: Bootstrap URL parsing is intentionally narrow

The parser supports sqlite and postgres URLs. For sqlite, it derives `sqlite_dir` from the URL path parent, which assumes the standard `deerflow.db` filename.

Required follow-up:

- Decide whether non-standard sqlite filenames are unsupported or need a richer bootstrap config.
- Add validation and an operator-facing error for unsupported sqlite URL shapes.

## Positive Checks

- DB mode no longer falls back to local `config.yaml` during Gateway startup.
- Runtime payload `database` is overridden by bootstrap DB settings, preserving the bootstrap boundary.
- `langgraph_runtime()` no longer blindly replaces a bootstrap-initialized engine.
- Focused tests seed a real SQLite DB and load `runtime_configs.app` through the startup helper.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py tests/test_gateway_db_config_startup.py -q
```

Result:

```text
10 passed, 1 warning in 0.53s
```

## Review Conclusion

This batch establishes the first end-to-end startup path for DB-backed AppConfig. It is still partial stateless mode: extensions, agents, memory, skills, MCP, and sandbox materialization remain to be implemented.
