# Agent Write Surfaces Review

Date: 2026-06-19

Reviewed change set:

- DB-mode write branches in agent HTTP router
- DB-mode user profile read/write behavior
- DB-mode `setup_agent` and `update_agent` persistence
- active-store helper additions in `agents_config.py`
- sync SQLite directory creation in `DbAgentStore`
- DB-mode tests and agent regression subset

## Findings

### P1: DB create is still check-then-save

The HTTP create path checks `agent_config_exists()` and then calls `save_agent_config()`. This is correct for normal serial requests but does not fully close concurrent duplicate creates.

Required follow-up:

- Add a `create_agent()` store method that inserts once and maps unique-constraint conflicts to HTTP 409.
- Keep `save_agent()` as the update/upsert path.

### P1: Sync DB store lifecycle is still per-call

The new helper functions instantiate `DbAgentStore()` per call through `_load_db_agent_store()`. This keeps the first slice simple but creates unnecessary engines and metadata checks under active traffic.

Required follow-up:

- Add app-lifecycle store caching keyed by database config.
- Add explicit disposal for sync engines or replace the sync store with the app async session factory.

### P2: Default-agent global `SOUL.md` remains file-backed

`setup_agent` only switches custom-agent writes to DB in DB mode. The default agent path still writes `SOUL.md` under the local base dir.

Required follow-up:

- Decide whether default SOUL is runtime config, a default-agent row, or a separate DB document.
- Include default SOUL in sandbox materialization.

### P2: Migration remains absent

DB mode can read/write custom agents and user profiles, but there is no importer for existing per-user `config.yaml`, `SOUL.md`, or `USER.md`.

Required follow-up:

- Add an idempotent migration CLI.
- Preserve legacy shared-agent collision rules during import.

## Positive Checks

- File-mode agent tests continue to pass.
- DB-mode HTTP create/update/delete no longer creates local agent files.
- DB-mode user profile writes DB rows instead of `USER.md`.
- DB-mode setup/update tools persist through the same DB-backed helper layer.
- `skills=[]` remains preserved through create/update.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_custom_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py tests/test_db_agent_store.py tests/test_create_deerflow_agent.py tests/test_lead_agent_prompt.py tests/test_lead_agent_model_resolution.py -q
uv --directory backend run ruff check tests/test_custom_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py packages/harness/deerflow/tools/builtins/setup_agent_tool.py packages/harness/deerflow/tools/builtins/update_agent_tool.py app/gateway/routers/agents.py
```

Result:

```text
184 passed, 2 warnings in 0.92s
All checks passed!
```

## Review Conclusion

This batch completes DB-mode read/write routing for custom agents, custom-agent tools, and user profile endpoints. The remaining risks are lifecycle/performance, concurrent create semantics, migration, and the unresolved default-agent global SOUL storage decision.
