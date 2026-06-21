# Default Agent SOUL DB Runtime Review

Date: 2026-06-19

## Scope

- `packages/harness/deerflow/persistence/agents/model.py`
- `packages/harness/deerflow/config/agent_store.py`
- `packages/harness/deerflow/config/agents_config.py`
- `packages/harness/deerflow/tools/builtins/setup_agent_tool.py`
- `packages/harness/deerflow/sandbox/materializer.py`
- `tests/test_db_agent_store.py`
- `tests/test_setup_agent_tool.py`
- `tests/test_sandbox_materializer.py`
- implementation log Batch 53

## Findings

### P2 Fixed: Default-agent SOUL no longer depends on the local global file in DB mode

Before this batch, DB config mode still had a default-agent gap: `agent_name=None` could read or write the top-level `SOUL.md` file. That kept a hidden local-file dependency in the stateless path.

The runtime now stores default-agent SOUL content in `default_agent_souls`, keyed by `owner_user_id`, and exposes it through:

- `DbAgentStore.load_default_agent_soul(...)`
- `DbAgentStore.save_default_agent_soul(...)`
- `load_agent_soul(None, user_id=...)` in DB mode
- `save_default_agent_soul(...)`

### P2 Fixed: Default-agent setup writes DB state instead of local runtime state

`setup_agent` now routes default-agent SOUL writes through the DB helper when DB config is enabled. The regression test verifies that a default-agent setup for user `alice` creates the DB value and does not create the legacy global `SOUL.md`.

### P2 Fixed: Sandbox materialization includes default-agent SOUL

The sandbox runtime context builder now materializes `agent/SOUL.md` for default-agent runs when a DB-backed default SOUL exists. This keeps AIOSandbox/runtime execution compatible with the file shape agents expect while keeping source-of-truth state in DB.

### P2 Documented In Batch 57: Legacy global SOUL ownership is an explicit compatibility mapping

The DB model is per-user. Legacy top-level `SOUL.md` has no user identity, so migration maps it to owner `default`. Batch 57 documents this operationally in `docs/harness-stateless-db-mode-operator-runbook.md` so operators treat `default` as a compatibility owner rather than a real account.

### P3 Fixed In Batch 56: Product/API semantics for default-agent management

Batch 56 adds dedicated default-agent SOUL endpoints:

- `GET /api/default-agent-soul`
- `PUT /api/default-agent-soul`

The endpoints use the same `agents_api.enabled` guard as custom agents and user profile routes, and route through the active store helpers so file mode writes `SOUL.md` while DB mode writes `default_agent_souls`.

## Verification

```bash
uv --directory backend run pytest tests/test_db_agent_store.py::test_db_agent_store_default_agent_soul tests/test_db_agent_store.py::test_agents_config_helpers_read_default_agent_soul_from_db_in_db_mode tests/test_setup_agent_tool.py::TestSetupAgentNoDataLoss::test_db_mode_default_agent_soul_is_written_to_db_not_global_file tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_reads_default_agent_soul -q
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py tests/test_custom_agent.py tests/test_update_agent_tool.py -q
```

Results:

- targeted default-agent SOUL tests: `4 passed, 1 warning`
- related agent/materializer regression set: `114 passed, 2 warnings`

## Conclusion

The default-agent SOUL runtime path now follows the same stateless contract as custom agents: DB is the source of truth, and sandbox-local files are generated runtime context rather than persistent configuration.
