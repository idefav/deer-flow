# Runtime State Import CLI Bootstrap Review

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- CLI `--apply --overwrite` behavior
- bootstrap DB URL integration
- `tests/test_import_runtime_state_to_db.py`
- implementation log Batch 45

## Findings

### P2 Fixed: CLI now honors `DEER_FLOW_DATABASE_URL`

The Python API already accepted an explicit `DatabaseConfig`, but the CLI entrypoint always constructed `DatabaseConfig()` directly. Because the default backend is `memory`, `--apply` could not target the intended DB from the operator shell.

`main()` now uses `get_bootstrap_database_config() or DatabaseConfig()`, matching the DB-mode startup bootstrap path.

### P2 Fixed: `--apply --overwrite` has a real subprocess smoke test

The new regression test executes `scripts/import_runtime_state_to_db.py` through `subprocess.run()` and sets `DEER_FLOW_DATABASE_URL=sqlite:///...`.

Coverage asserts:

- the CLI exits successfully.
- the JSON report points at the intended sqlite DB.
- overwrite accounting reports the existing `user_profile` conflict.
- the DB profile row is replaced.

### P2: Environment-based DB selection is intentionally reused

Using `DEER_FLOW_DATABASE_URL` keeps migration behavior aligned with runtime DB bootstrap. This avoids adding a second DB selection mechanism before the operator workflow is finalized.

Potential future improvement:

- Add an explicit `--database-url` option for one-off migration runs, while keeping the env var as the shared default.

### P3: CLI still emits warning noise on stderr

The subprocess test allows stderr warnings and validates the JSON report from stdout. The warning noise comes from existing runtime imports and config checks, not from the migration output contract.

Potential future improvement:

- Reduce import-time warning noise or move operator warnings into the JSON report for cleaner scripting.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_uses_bootstrap_database_url -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Results:

- targeted CLI smoke test: `1 passed, 1 warning`
- related regression set: `25 passed, 1 warning`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

The migration command's real CLI path can now target DB-backed stores through the same bootstrap URL used by runtime DB mode. This makes `--apply --overwrite` testable and usable from an operator shell without writing custom Python glue.
