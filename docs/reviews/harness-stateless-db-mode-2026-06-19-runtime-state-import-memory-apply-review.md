# Runtime State Import Memory Apply Review

Date: 2026-06-19

Reviewed change set:

- memory apply path in `scripts/import_runtime_state_to_db.py`
- memory scope import test

## Findings

### P1 Fixed: Migration apply can import memory hierarchy into DB

The import script now writes all discovered memory files through `DbMemoryStorage.save()`. Legacy global memory maps to owner `default`, legacy agent memory maps to owner `default` plus agent scope, and per-user memory maps to its user id.

Coverage added:

- global memory.
- legacy agent memory.
- per-user memory.
- per-user agent memory.

### P1: Import conflict policy is still overwrite/update

`DbMemoryStorage.save()` updates existing rows and increments revision. The migration script does not yet preflight whether DB already has memory for the same owner/scope.

Required follow-up:

- Report owner/scope conflicts in dry-run.
- Add an explicit overwrite/apply policy before production import.

### P2: Memory shape validation remains light

The apply path checks JSON parsing and then delegates to `DbMemoryStorage`. It does not yet validate that the JSON matches the expected memory schema.

Required follow-up:

- Validate memory payloads during dry-run.
- Include schema warnings for partial/legacy memory shapes.

## Positive Checks

- The migration uses `DbMemoryStorage.save()` rather than writing ORM rows directly.
- Storage-level revision freshness and updater tests remain green.
- Existing memory prompt injection tests remain behavior-compatible.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_db_memory_storage.py tests/test_memory_updater.py tests/test_memory_prompt_injection.py -q
```

Result:

```text
79 passed, 1 warning in 0.50s
```

```bash
uv --directory backend run ruff check tests/test_import_runtime_state_to_db.py scripts/import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

## Review Conclusion

The migration command now imports memory at the same hierarchy used by runtime DB mode. Remaining migration apply work is skills, plus conflict and schema preflight.
