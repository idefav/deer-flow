# Runtime Config Foundation Review

Date: 2026-06-19

Reviewed change set:

- bootstrap env helper
- `runtime_configs` ORM model
- runtime config repository
- runtime config focused tests
- implementation log entry

## Findings

### P1: Bootstrap DB settings are still only a URL string

The first implementation adds `DEER_FLOW_DATABASE_URL` parsing as the minimal bootstrap boundary. This is enough to unblock DB-backed config source work, but it does not yet construct the existing `DatabaseConfig` shape or initialize the persistence engine from bootstrap settings.

Required follow-up:

- Define whether DB mode accepts only a SQLAlchemy async URL or also structured env such as backend/sqlite_dir/postgres_url.
- Add a small adapter from bootstrap settings to `init_engine()`.
- Keep `runtime_configs.database` as visibility/audit data only, not active engine source.

### P1: Store is not connected to AppConfig yet

`RuntimeConfigRepository` is intentionally isolated. It does not change `get_app_config()` or current file mode behavior.

Required follow-up:

- Add `ConfigSource` abstraction.
- Add `DbConfigSource` that reads key `app` through this repository.
- Add tests proving file mode remains default and DB mode is opt-in.

### P2: Concurrency semantics are simple upsert only

Current `upsert()` increments revision for each successful write but does not expose compare-and-swap semantics.

Required follow-up:

- For admin config writes, decide whether blind replacement is acceptable.
- If concurrent config editing matters, add optional `expected_revision` and conflict tests.

## Positive Checks

- The model is registered in `deerflow.persistence.models`, so `Base.metadata.create_all()` can discover it.
- `content_hash` uses canonical JSON with sorted keys and compact separators.
- Repository returns deep-copied payloads, preventing caller mutation from changing cached row payloads.
- The first implementation does not affect existing file config loading.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py -q
```

Result:

```text
7 passed, 1 warning in 0.19s
```

## Review Conclusion

This batch is a safe foundation for the next slice. It should not be treated as DB mode support by itself; it only provides the storage primitive and bootstrap mode helper needed to build `DbConfigSource`.
