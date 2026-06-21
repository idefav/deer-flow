# Runtime State Import App Config Preflight Review

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- app config validation in runtime-state import preflight
- `tests/test_import_runtime_state_to_db.py`
- implementation log Batch 43

## Findings

### P2 Fixed: Dry-run now validates app config semantics

The import command previously checked whether `config.yaml` was syntactically valid YAML but did not validate the payload with `AppConfig`. A malformed-but-parseable config could pass dry-run and fail later during apply or runtime load.

Preflight now calls `AppConfig.from_payload(..., apply_singletons=False)` and reports validation failures as `app_config` source errors.

### P2 Fixed: Dry-run remains non-mutating on app config validation failure

The regression test uses an invalid `models` shape and verifies that:

- `preflight.ok` is false.
- the error is attached to `app_config`.
- the sqlite DB file is not created.

This preserves the migration command's safety contract: dry-run validates source state without initializing DB stores.

### P2: Validation intentionally avoids singleton side effects

The preflight path passes `apply_singletons=False`, so validation reuses runtime config semantics without applying global title, summarization, memory, guardrail, checkpointer, stream bridge, ACP, or logger-adjacent singleton changes.

### P3: Error payload is still human-readable text

The preflight report stores the exception string. This is enough for operator review and keeps the report format consistent with existing agent and memory validation errors.

Potential future improvement:

- Preserve structured Pydantic error details if the migration tool gains automated remediation or UI rendering.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_config_sources.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Results:

- targeted app config preflight test: `1 passed, 1 warning`
- related regression set: `24 passed, 1 warning`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

The migration dry-run now catches invalid app config semantics before any DB mutation. This closes the earlier parser-level-only validation gap for `config.yaml`.
