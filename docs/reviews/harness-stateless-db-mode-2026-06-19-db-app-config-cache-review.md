# DB AppConfig Cache Loader Review

Date: 2026-06-19

Reviewed change set:

- `load_and_cache_db_app_config()`
- DB-mode guard inside `get_app_config()`
- cache source kind tracking
- DB AppConfig cache tests
- implementation log Batch 4 entry

## Findings

### P1: Startup wiring is still missing

The loader exists, but Gateway startup does not yet call it. In DB mode, a real process still needs bootstrap engine initialization and repository construction before the cached config is available.

Required follow-up:

- Add startup logic that reads bootstrap DB settings.
- Initialize persistence engine without depending on DB-loaded `AppConfig`.
- Build `RuntimeConfigRepository` from the session factory.
- Call `load_and_cache_db_app_config()` before runtime code calls `get_app_config()`.

### P1: DB config cache does not auto-refresh yet

The cache can be refreshed by calling `load_and_cache_db_app_config()` again, but `get_app_config()` does not poll DB revision.

Required follow-up:

- Add an explicit `get_cached_app_config_revision()` or similar helper.
- Add a lightweight revision check path for admin-triggered reload or startup refresh.
- Avoid DB reads in arbitrary synchronous hot paths.

### P1: Extensions still come from the caller or default empty config

DB-loaded AppConfig can accept an injected `ExtensionsConfig`, but there is no DB-backed `ExtensionsConfigSource` yet.

Required follow-up:

- Implement `ExtensionsConfigSource` after config source foundation.
- Ensure DB mode startup passes the same extensions view into `load_and_cache_db_app_config()`.

## Positive Checks

- Synchronous `get_app_config()` now has a DB-mode fail-fast guard instead of accidentally falling back to local files.
- `set_app_config()` remains a custom override and still works even if env says DB mode.
- File mode behavior remains separate and cache source kind is reset correctly.
- Tests cover preload, sync access after preload, and explicit refresh.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_config_sources.py -q
```

Result:

```text
9 passed, 1 warning in 0.19s
```

## Review Conclusion

This batch closes the async/sync boundary for DB-backed AppConfig loading. It is a necessary foundation, but DB mode is still not end-to-end until startup wiring and DB-backed extensions are added.
