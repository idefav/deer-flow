# Runtime State Import Default Agent SOUL Review

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- default-agent SOUL inventory
- default-agent SOUL apply path
- DB conflict reporting
- `tests/test_import_runtime_state_to_db.py`
- implementation log Batch 54

## Findings

### P2 Fixed: Legacy top-level `SOUL.md` is included in migration inventory

The migration inventory now detects `state_dir/SOUL.md` and reports it under `default_agent_souls` with:

- `owner_user_id: default`
- `legacy: true`
- source `path`

The summary also includes `default_agent_souls`, so dry-run output makes the default-agent migration unit visible to operators.

### P2 Fixed: Apply writes default-agent SOUL into DB

`--apply` now reads each reported default-agent SOUL file and writes it through `DbAgentStore.save_default_agent_soul(...)`. The apply summary reports `default_agent_souls`, and the regression test verifies the stored value through `load_default_agent_soul("default")`.

### P2 Fixed: Conflict reporting includes existing default-agent SOUL rows

Conflict detection now checks `default_agent_souls` when the table exists and reports an existing row as:

```text
resource: default_agent_soul
owner_user_id: <owner>
```

This keeps default-agent SOUL aligned with the existing import preflight model for DB-backed resources.

### P2 Fixed In Batch 55: Overwrite-specific replacement coverage

Batch 55 adds a focused overwrite regression that seeds an existing `default_agent_souls` row, runs `--apply --overwrite`, and asserts:

- the preflight conflict is reported as `default_agent_soul`.
- `overwritten_by_resource` counts `default_agent_soul`.
- the stored SOUL content is replaced.
- the row revision advances from 1 to 2.

### P3 Fixed In Batch 57: Legacy owner mapping is documented in the operator runbook

The migration maps global `SOUL.md` to owner `default` because the legacy file has no per-user identity. Batch 57 documents this in `docs/harness-stateless-db-mode-operator-runbook.md`, including how teams should treat `default` as a compatibility owner and update real users through `PUT /api/default-agent-soul` after migration.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_agents_and_user_profiles tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_default_agent_soul -q
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check packages/harness/deerflow/persistence/agents/model.py packages/harness/deerflow/persistence/models/__init__.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py packages/harness/deerflow/tools/builtins/setup_agent_tool.py packages/harness/deerflow/sandbox/materializer.py scripts/import_runtime_state_to_db.py tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py tests/test_import_runtime_state_to_db.py
```

Results:

- targeted default-agent SOUL migration tests: `3 passed, 1 warning`
- combined regression set: `54 passed, 1 warning`
- ruff: `All checks passed`

## Conclusion

Legacy default-agent SOUL is now part of the DB migration story. After import, the default agent can run in DB/stateless mode without retaining `SOUL.md` as persistent local state.
