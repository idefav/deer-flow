# Default Agent SOUL Revision Review

Date: 2026-06-20

## Scope

- `packages/harness/deerflow/config/agent_store.py`
- `packages/harness/deerflow/config/agents_config.py`
- `app/gateway/routers/agents.py`
- `tests/test_custom_agent.py`
- default-agent SOUL operator runbook notes
- implementation log Batch 59

## Findings

### P2 Fixed: Default-agent SOUL API exposes revision metadata

`GET /api/default-agent-soul` and `PUT /api/default-agent-soul` now return a `revision` field. Missing content reports revision `0`; DB-backed rows report the `default_agent_souls.revision` value.

### P2 Fixed: Conditional writes prevent silent stale overwrites

`PUT /api/default-agent-soul` accepts optional `expected_revision`. When provided, the save only succeeds if the active store revision still matches.

Stale writes return 409 and preserve the existing content.

### P2 Fixed: File mode remains compatible while gaining the same guard

File mode uses the top-level `SOUL.md` mtime nanoseconds as the revision. Existing callers that omit `expected_revision` keep last-write-wins behavior.

### P2 Fixed: DB mode uses the existing row revision contract

DB mode checks `default_agent_souls.revision` and increments it on successful updates. The test covers create at revision 1, conditional update to revision 2, and stale revision 1 rejection.

## Verification

```bash
uv --directory backend run pytest tests/test_custom_agent.py::TestDefaultAgentSoulAPI tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_uses_db_not_soul_md_file tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_db_update_rejects_stale_revision -q
uv --directory backend run pytest tests/test_custom_agent.py tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py -q
uv --directory backend run ruff check app/gateway/routers/agents.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py tests/test_custom_agent.py
git diff --check
```

Results:

- targeted default-agent SOUL revision tests: `6 passed, 2 warnings`
- related agent/API/default-SOUL regression set: `101 passed, 2 warnings`
- ruff: `All checks passed`
- diff check: no output

## Conclusion

Default-agent SOUL admin writes now have an optimistic concurrency option. This keeps backward-compatible last-write-wins behavior for existing callers while giving admin clients a safe edit workflow.
