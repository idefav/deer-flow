# Harness Stateless DB Mode Review: Runtime State Import Preflight

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- runtime import tests
- implementation log Batch 37

## Findings

### P1 Fixed: Source errors are reported before apply

Inventory now records parse/shape errors for source files that previously would fail later or import inconsistently:

- invalid agent `config.yaml`
- invalid memory JSON or lightweight memory shape errors
- invalid skill `SKILL.md` frontmatter
- skill directory name mismatch with frontmatter `name`

Dry-run returns these in `preflight.errors`.

### P1 Fixed: Existing DB rows are treated as conflicts

Preflight now checks existing DB state for the import targets:

- app runtime config
- extensions runtime config
- user profiles
- custom agents
- memory scopes
- custom skills

`--apply` fails before writing when conflicts exist, preventing silent overwrite of live DB state.

### P1 Fixed: Dry-run remains non-mutating for new sqlite targets

When the sqlite DB file does not exist, conflict checks are skipped and the dry-run does not create the database file. This keeps the migration planning command safe to run before operators commit to an apply.

### P2: There is no explicit overwrite mode yet

The conflict policy is now conservative. Operators who intentionally want to replace existing DB rows must clear rows manually or wait for an explicit overwrite/import-replace mode.

Recommendation:

- Add `--overwrite` or `--replace-existing`.
- Include per-resource conflict counts and replaced counts in the apply summary.
- Require the flag only for existing-row conflicts, not for source validation errors.

### P2: Agent semantic validation is still partial

Agent config YAML parse errors are caught, but deeper `AgentConfig` validation still happens during apply. This is acceptable for the current batch, but preflight could catch more issues earlier.

Recommendation:

- Run `AgentConfig.model_validate()` during preflight.
- Report validation details under `preflight.errors`.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py tests/test_sandbox_file_operation_tools.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py packages/harness/deerflow/sandbox/tools.py packages/harness/deerflow/sandbox/materializer.py packages/harness/deerflow/sandbox/middleware.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py
git diff --check
```

Results:

- `8 passed, 1 warning`
- `48 passed, 1 warning`
- `ruff check`: passed
- `git diff --check`: no output

## Conclusion

The migration command now has a conservative safety gate: dry-run reports malformed sources, and apply refuses existing-row conflicts instead of overwriting. The next operator-facing improvement is an explicit overwrite mode with clear replacement accounting.
