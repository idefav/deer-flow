# Harness Stateless DB Mode Review: Runtime State Import Overwrite

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- runtime import tests
- implementation log Batch 38

## Findings

### P2 Fixed: Explicit overwrite mode now exists

The migration command now accepts `overwrite=True` through the Python API and `--overwrite` through the CLI. Existing DB conflicts remain visible in `preflight.conflicts`, but they no longer block `--apply` when overwrite is enabled.

### P2 Fixed: Apply summary reports overwrite count

When overwrite mode is used, the apply summary includes `overwritten_conflicts`. This gives operators a basic replacement count instead of making overwrite behavior implicit.

### P2 Fixed: Apply summary reports overwrite counts by resource

The apply summary now also includes `overwritten_by_resource`, a deterministic map keyed by preflight conflict resource. This makes mixed imports easier to audit; for example, a run replacing one user profile and one memory row reports `{"memory": 1, "user_profile": 1}`.

### P2 Fixed: Agent semantic validation moved into preflight

Agent YAML parse errors were already reported. This batch also validates the effective payload with `AgentConfig.model_validate()` and validates the configured agent name before apply. Invalid agent config now appears in `preflight.errors` during dry-run, and dry-run still avoids creating a sqlite DB file when the DB does not exist.

### P2: Replacement coverage is still incomplete for app config and extensions/MCP

The implementation allows all existing-row conflicts to be overwritten when the operator opts in. Tests now cover user profile, memory, custom agent, and custom skill replacement plus per-resource accounting. App config and extensions/MCP still use existing upsert/save logic and should get explicit coverage before production migration runbooks rely on those replacement paths.

Recommendation:

- Add tests for app config and extensions/MCP with `overwrite=True`.
- Consider apply-function replacement counts if operators need audit-grade summaries beyond preflight conflict counts.

### P2: Source errors still block overwrite, intentionally

`--overwrite` only relaxes existing-row conflicts. Source parse and validation errors still block apply. This is the right default because overwrite should not turn malformed source state into DB state.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_existing_db_conflicts -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_reports_conflicts_by_resource -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_agents_and_custom_skills -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_agent_config_semantics -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Results:

- `1 passed, 1 warning`
- `1 passed, 1 warning`
- `1 passed, 1 warning`
- `1 passed, 1 warning`
- `12 passed, 1 warning`

## Conclusion

The migration command now matches the documented conservative default plus explicit overwrite model: refuse conflicts by default, replace only when requested, still fail on malformed source state, and report replacement counts by resource. The remaining work is explicit app config and extensions/MCP replacement coverage.
