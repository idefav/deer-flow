# ConfigSource Payload Layer Review

Date: 2026-06-19

Reviewed change set:

- `deerflow.config.sources`
- `backend/tests/test_config_sources.py`
- implementation log Batch 2 entry

## Findings

### P1: DB source is async while current `get_app_config()` is sync

`DbConfigSource.load_app_config_payload()` is async because it uses the existing async persistence repository. This is acceptable for the payload layer, but it means DB-backed `AppConfig` loading still needs an explicit composition decision before `get_app_config()` is wired to DB mode.

Required follow-up:

- Either add an async startup loader for DB mode and keep `get_app_config()` as a cached sync accessor afterward.
- Or introduce a carefully scoped sync bridge for config loading.
- Do not call async SQLAlchemy repositories directly from synchronous hot paths.

### P1: Source layer intentionally excludes extensions

`ConfigSource` now owns only app runtime config. This follows the refinement decision that MCP and skill enabled state should later go through `ExtensionsConfigSource`.

Required follow-up:

- Implement `ExtensionsConfigSource` separately.
- Ensure runtime MCP paths do not bypass that future facade.

### P2: File source uses mtime ns as revision

`FileConfigSource` uses `st_mtime_ns` as its revision and canonical JSON hash as the stronger content identity. This is good enough for compatibility and tests, but DB mode should rely on DB revision instead.

Required follow-up:

- When integrating source caching, compare `content_hash` as the stronger invalidation signal.
- Treat file revision as advisory metadata.

## Positive Checks

- `RevisionedPayload` is now shared by file and DB sources.
- File source does not mutate or cache caller-visible payload objects.
- DB source reuses `RuntimeConfigRepository`, keeping DB row semantics centralized.
- Existing `get_app_config()` behavior is unchanged in this batch.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_config_sources.py -q
```

Result:

```text
4 passed, 1 warning in 0.18s
```

## Review Conclusion

This batch creates the intended app config source boundary. The next implementation step should validate `RevisionedPayload` into `AppConfig` without yet mixing extensions/MCP into the same source abstraction.
