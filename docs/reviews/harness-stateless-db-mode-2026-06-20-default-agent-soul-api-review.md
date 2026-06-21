# Default Agent SOUL API Review

Date: 2026-06-20

## Scope

- `app/gateway/routers/agents.py`
- default-agent SOUL HTTP management surface
- file-mode and DB-mode persistence behavior
- `tests/test_custom_agent.py`
- implementation log Batch 56

## Findings

### P2 Fixed: Default-agent SOUL now has a first-class API surface

The gateway now exposes dedicated endpoints for the default agent:

- `GET /api/default-agent-soul`
- `PUT /api/default-agent-soul`

This closes the management gap left after the runtime and migration paths became DB-backed. API clients no longer need to misuse custom-agent routes or write local `SOUL.md` directly to manage the default agent identity.

### P2 Fixed: DB mode writes `default_agent_souls`, not local `SOUL.md`

The DB-mode regression test writes through `PUT /api/default-agent-soul`, reads through `GET /api/default-agent-soul`, verifies `DbAgentStore.load_default_agent_soul("test-user-autouse")`, and asserts no top-level `SOUL.md` file is created.

### P2 Fixed: File mode remains behavior-compatible

File-mode tests verify:

- missing default SOUL returns `content: null`.
- `PUT /api/default-agent-soul` writes top-level `SOUL.md`.
- subsequent `GET /api/default-agent-soul` returns the saved content.

### P2 Fixed: Management API guard applies consistently

The default-agent SOUL routes use the same `agents_api.enabled` guard as custom-agent and user-profile routes. Disabled API tests verify both GET and PUT return 403.

### P2 Fixed In Batch 59: Revision metadata and conditional writes are available

The API now returns `revision` from both GET and PUT. PUT accepts `expected_revision`; when the current revision differs, the route returns 409 and leaves the existing SOUL unchanged.

This closes the concurrent admin editing gap without forcing callers into conditional writes. Omitting `expected_revision` preserves last-write-wins compatibility.

## Verification

```bash
uv --directory backend run pytest tests/test_custom_agent.py::TestDefaultAgentSoulAPI tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_uses_db_not_soul_md_file tests/test_custom_agent.py::TestAgentsApiDisabled::test_default_agent_soul_routes_return_403 -q
uv --directory backend run pytest tests/test_custom_agent.py tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py -q
uv --directory backend run ruff check app/gateway/routers/agents.py tests/test_custom_agent.py
git diff --check
```

Results:

- targeted default-agent SOUL API tests: `5 passed, 2 warnings`
- related agent/API/default-SOUL regression set: `99 passed, 2 warnings`
- ruff: `All checks passed`
- diff check: no output

## Conclusion

Default-agent SOUL management now has an explicit HTTP surface aligned with the stateless DB contract. File mode still writes the legacy `SOUL.md`, while DB mode persists per-user default SOUL content in `default_agent_souls`.
