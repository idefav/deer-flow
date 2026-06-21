# Runtime State Import Error Codes Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P3 Fixed: Preflight errors now include stable machine-readable codes

Before Batch 87, `preflight.errors` exposed `resource`, location fields, and a human-readable `error` string. Operators could read failures, but automation had to match exception text. Batch 87 adds a stable `code` field to source/preflight errors for invalid app config, extensions config, agent config, memory JSON, skill metadata, oversized skill files, and oversized skill packages.

### P2 Guarded: Existing human-readable diagnostics are preserved

The change is additive. Existing `resource`, `path`, owner/name/category fields, and readable `error` messages remain in the report, so current operator workflows and migration logs keep their shape while scripts can switch to `code`.

### P2 Guarded: Critical migration blockers have code coverage

The focused regression set covers the errors most likely to block stateless migration dry-run:

- `invalid_memory`
- `invalid_app_config`
- `invalid_extensions_config`
- `invalid_agent_config`
- `skill_file_too_large`
- `skill_package_too_large`

The implementation also assigns `invalid_skill_metadata` for malformed custom skill metadata.

### P3 Remaining: CLI warning noise is separate from structured report fields

This batch improves JSON report diagnostics. It does not remove unrelated import-time warnings that can still appear on stderr in some CLI subprocess paths. Those warnings are already tolerated by the CLI tests and remain lower-priority cleanup.

## Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_invalid_memory_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_agent_config_semantics -q
```

Result:

```text
6 failed, 1 warning in 0.23s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_invalid_memory_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_agent_config_semantics -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
6 passed, 1 warning in 0.17s
23 passed, 1 warning in 1.76s
All checks passed!
4807 passed, 36 skipped, 12 warnings in 84.85s
All checks passed!
git diff --check produced no output.
```

## Conclusion

Runtime-state migration preflight now has both operator-readable messages and stable automation-friendly error codes. This closes the earlier human-readable-only diagnostics gap without changing the existing report shape.
