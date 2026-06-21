# DB MCP Runtime Review

Date: 2026-06-19

Reviewed change set:

- `DbExtensionsConfigStore`
- DB-mode `get_extensions_config()` / `reload_extensions_config()`
- DB-mode MCP PUT path
- active-source reload in MCP tools, ACP helpers, and skill enabled-state merge
- MCP/Extensions tests

## Findings

### P1: MCP cache reset is still process-local

DB mode persists the config in a shared database, but `reset_mcp_tools_cache()` only affects the current Gateway process. Other workers or nodes can keep stale MCP tools.

Required follow-up:

- Add revision-aware cache keys using `runtime_configs.extensions.revision`.
- Add pub/sub, polling, or request-time revision checks for multi-process deployments.

### P1: Concurrent MCP updates have last-write-wins behavior

The DB store increments revision but the API does not require the caller to submit an expected revision.

Required follow-up:

- Add ETag/revision to GET response.
- Require expected revision on PUT or add compare-and-swap in `DbExtensionsConfigStore`.

### P2: MCP remains embedded in `runtime_configs.extensions`

Keeping MCP and skill enabled state in one JSON payload preserves compatibility. It also makes per-server audit, ownership, and secret rotation harder.

Required follow-up:

- Decide before production migration whether to split MCP servers into dedicated rows.
- If staying JSON-backed, add JSON schema versioning and migration functions.

### P2: Skill enabled toggle still writes file in DB mode

Runtime skill loading now reads active extensions config, but `/api/skills/{skill_name}` still writes `extensions_config.json`.

Required follow-up:

- Route skill enabled updates through `DbExtensionsConfigStore` in DB mode.

## Positive Checks

- MCP PUT in DB mode preserves masked secrets.
- Top-level `mcpInterceptors` and skills state are preserved.
- MCP tool loading no longer hard-codes file reads.
- File-mode MCP tests still pass.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_mcp_config_secrets.py tests/test_extensions_config_sources.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py -q
uv --directory backend run ruff check tests/test_extensions_config_sources.py tests/test_mcp_config_secrets.py packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/extensions_config.py app/gateway/routers/mcp.py packages/harness/deerflow/mcp/tools.py packages/harness/deerflow/tools/tools.py packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py packages/harness/deerflow/skills/storage/skill_storage.py
```

Result:

```text
94 passed, 1 warning in 0.83s
All checks passed!
```

## Review Conclusion

This batch removes the largest MCP file dependency in DB mode by making runtime extension reads and MCP API writes use DB-backed runtime config. Remaining work is cache coherency, concurrency control, and deciding whether JSON-backed MCP config is sufficient for production migration.
