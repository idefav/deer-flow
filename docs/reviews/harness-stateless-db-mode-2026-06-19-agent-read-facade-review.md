# Agent Read Facade Review

Date: 2026-06-19

Reviewed change set:

- DB-mode routing in `agents_config.py`
- DB-mode helper regression test
- agent read regression subset
- implementation log Batch 9 entry

## Findings

### P1: Write paths still bypass the DB store

`load_agent_config()`, `load_agent_soul()`, and `list_custom_agents()` now switch to DB in DB mode, but HTTP create/update/delete endpoints still write local files.

Required follow-up:

- Add mode-aware write facade.
- Update agent router create/update/delete paths.
- Update setup/update agent tools.

### P1: Store instances are created per helper call

The current DB read helpers instantiate `DbAgentStore()` lazily per call. This is acceptable for the first read-facade slice but inefficient.

Required follow-up:

- Add a cached store provider keyed by database config or application lifecycle.
- Add disposal/lifecycle handling for sync DB engines.

### P2: Default-agent `SOUL.md` remains file-backed

`load_agent_soul(None)` still uses the file path even in DB mode. This preserves existing default-agent behavior but does not solve fully stateless default SOUL/profile materialization.

Required follow-up:

- Decide whether default `SOUL.md` belongs in runtime config, user profile, or a separate DB row.
- Include it in sandbox materialization design.

## Positive Checks

- File mode keeps existing helper behavior and legacy fallback.
- DB mode can read custom agent config, soul, and list results without local agent files.
- Existing agent and lead-agent tests continue to pass.
- Agent name validation remains centralized.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_custom_agent.py tests/test_create_deerflow_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py tests/test_lead_agent_prompt.py tests/test_lead_agent_model_resolution.py -q
uv --directory backend run ruff check tests/test_db_agent_store.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py packages/harness/deerflow/persistence/agents packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
179 passed, 2 warnings in 0.85s
All checks passed!
```

## Review Conclusion

This batch connects DB-backed custom-agent storage to runtime reads. The agent subsystem is still incomplete for stateless mode until all write paths and migration import are routed through the store abstraction.
