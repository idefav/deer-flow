# DB Agent Store Foundation Review

Date: 2026-06-19

Reviewed change set:

- `CustomAgentRow`
- `UserProfileRow`
- `DbAgentStore`
- model registration
- DB agent store tests
- implementation log Batch 8 entry

## Findings

### P1: Runtime routers and tools still use file-backed agent helpers

This batch adds the DB store foundation but does not yet route `agents.py`, setup agent tool, update agent tool, or assistant listing through a store facade.

Required follow-up:

- Introduce an `AgentStore` facade that selects file or DB mode.
- Route list/get/create/update/delete HTTP handlers through the facade.
- Route setup/update agent tools through the same facade.

### P1: Legacy fallback is not represented in `DbAgentStore`

The existing file implementation reads per-user agents first and falls back to legacy shared agents. `DbAgentStore` only reads DB-owned rows.

Required follow-up:

- Keep a `FileAgentStore` wrapper for file mode and migration fallback.
- In DB mode after migration, decide whether legacy shared agents are unsupported or imported into a system/default owner.

### P1: Sync DB engine repeats the Memory V1 tradeoff

`DbAgentStore` uses a synchronous SQLAlchemy engine so callers can remain synchronous like the current file helpers.

Required follow-up:

- Document separate sync connection pools for DB-backed file-replacement stores.
- Add lifecycle/disposal if these stores become long-lived singletons.

### P2: No compare-and-swap revision semantics yet

Rows have revision counters, but save operations are last-write-wins.

Required follow-up:

- Add expected revision support before exposing concurrent HTTP writes in DB mode.
- Return a conflict response from the router if an admin UI edits stale agent data.

## Positive Checks

- `skills=None` and `skills=[]` are preserved through DB round trip.
- User isolation is tested.
- Agent names reuse the existing validation helper.
- User profile storage is included in the same foundation.
- Existing file-backed custom agent tests continue to pass.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_custom_agent.py tests/test_create_deerflow_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py -q
uv --directory backend run ruff check tests/test_db_agent_store.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/persistence/agents packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
143 passed, 2 warnings in 0.75s
All checks passed!
```

## Review Conclusion

This batch establishes DB persistence for agents and profiles, but the application still needs a mode-aware agent facade before DB/stateless runtime can stop depending on local agent files.
