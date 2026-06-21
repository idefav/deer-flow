# Skill Package Size Preflight Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P2 Fixed: Migration now blocks oversized custom skill packages

Before Batch 86, import dry-run blocked individual support files that exceeded `DEER_FLOW_DB_SKILL_FILE_MAX_BYTES`, but it did not guard against many small files producing an oversized DB payload. Batch 86 adds a package-level threshold, `DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES`, calculated from `SKILL.md` plus all support files.

### P2 Guarded: Many-small-files DB bloat is covered

The new test creates two support files that are each below the per-file threshold while the full package exceeds the package threshold. Dry-run now reports `skill-package-too-large`, blocks preflight, and avoids creating the DB.

### P2 Preserved: Existing single-file guard remains intact

The existing oversized support-file test still passes. The new helper keeps environment parsing non-negative and preserves the old `DEER_FLOW_DB_SKILL_FILE_MAX_BYTES` behavior.

### P2 Remaining: V1 still uses JSON-backed custom skill storage

This batch makes the V1 JSON storage shape safer to migrate into. It does not change the documented later-phase normalized `skills` / `skill_files` schema boundary.

## Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package -q
```

Result:

```text
1 failed, 1 warning in 0.20s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
2 passed, 1 warning in 0.17s
23 passed, 1 warning in 1.80s
All checks passed!
4807 passed, 36 skipped, 12 warnings in 84.12s
All checks passed!
git diff --check produced no output.
```

## Conclusion

Runtime-state migration now catches both single-file and aggregate custom-skill size risks before DB apply, reducing the chance of importing oversized `custom_skills` JSON payloads.
