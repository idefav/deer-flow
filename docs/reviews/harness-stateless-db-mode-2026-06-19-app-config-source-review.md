# AppConfig Source Validation Review

Date: 2026-06-19

Reviewed change set:

- `AppConfig.from_payload()`
- `AppConfig.from_source()`
- `AppConfig.from_file()` refactor through `from_payload()`
- new source validation tests
- implementation log Batch 3 entry

## Findings

### P1: DB mode still needs an async composition boundary

`AppConfig.from_source()` currently consumes synchronous sources only. That keeps file-source validation simple, but `DbConfigSource` remains async.

Required follow-up:

- Add an explicit async DB config loader that awaits `DbConfigSource.load_app_config_payload()` and calls `AppConfig.from_payload()`.
- Decide whether startup initializes DB config asynchronously and then stores a sync cached `AppConfig`.
- Do not make `get_app_config()` call async DB repositories directly.

### P1: `from_payload()` applies singleton side effects by default

This matches existing `from_file()` behavior, but callers that only want validation need to pass `apply_singletons=False`.

Required follow-up:

- Use `apply_singletons=False` in migration dry-run or validation-only tools.
- Add tests around validation-only usage before migration tooling lands.

### P2: Config version checking remains file-only

`from_payload()` does not run `_check_config_version()` because DB payloads do not have a neighboring `config.example.yaml` path.

Required follow-up:

- Store and validate schema version through `runtime_configs.schema_version`.
- Keep YAML `config_version` warning behavior in `from_file()`.

## Positive Checks

- Existing file loading still reads YAML and extensions from their current file source.
- Shared validation now has a single path for file payloads and future DB payloads.
- Tests assert caller payloads are not mutated by default database insertion.
- Source abstraction remains scoped to app runtime config and does not absorb extensions/MCP.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_config_sources.py -q
```

Result:

```text
6 passed, 1 warning in 0.17s
```

## Review Conclusion

This batch moves the design from storage/source primitives to AppConfig validation. It is still not a DB mode switch; the next safe step is an async DB config loader plus cache/reload tests.
