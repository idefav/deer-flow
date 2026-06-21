# Runtime State Import Database URL CLI Review

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- `deerflow.config.bootstrap`
- CLI `--database-url` behavior
- `tests/test_import_runtime_state_to_db.py`
- implementation log Batch 46

## Findings

### P2 Fixed: CLI supports an explicit one-off DB target

The migration CLI now accepts `--database-url`. This is useful for operator workflows where exporting `DEER_FLOW_DATABASE_URL` is inconvenient or risky because it could leak into later shell commands.

The explicit argument takes precedence over `DEER_FLOW_DATABASE_URL`. If neither is provided, the CLI keeps the previous `DatabaseConfig()` fallback.

### P2 Fixed: Bootstrap URL parsing is shared

The URL parsing logic now lives in `database_config_from_url(url)`, and `get_bootstrap_database_config()` delegates to it.

This keeps supported URL forms consistent across:

- DB-mode startup bootstrap.
- migration CLI `DEER_FLOW_DATABASE_URL`.
- migration CLI `--database-url`.

### P2 Fixed: CLI argument path has subprocess coverage

The new test runs the real script entrypoint with `--database-url sqlite:///... --apply --overwrite`, with `DEER_FLOW_DATABASE_URL` removed from the child environment.

Coverage asserts:

- the CLI exits successfully.
- the JSON report points at the explicit sqlite DB.
- overwrite accounting reports the existing `user_profile` conflict.
- the DB profile row is replaced.

### P3: Invalid URL diagnostics remain raw exceptions

Invalid URLs still raise `ValueError` from the shared parser. This is acceptable for the current internal migration command, but a public operator interface should format invalid URL errors without a traceback.

Potential future improvement:

- Catch `ValueError` in `main()` and call `parser.error(str(exc))`.

### P3: CLI stderr still includes import-time warnings

The subprocess smoke tests validate JSON from stdout and tolerate warning noise on stderr. The warnings predate this change.

Potential future improvement:

- Move migration warnings into the JSON report or defer noisy runtime imports until after argparse completes.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_accepts_database_url_argument -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py packages/harness/deerflow/config/bootstrap.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Results:

- targeted CLI `--database-url` smoke test: `1 passed, 1 warning`
- related regression set: `26 passed, 1 warning`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

The migration CLI now supports both environment-based and explicit one-off DB target selection, using the same URL parsing logic as DB-mode startup. This makes the operator path more predictable while keeping existing behavior compatible.
