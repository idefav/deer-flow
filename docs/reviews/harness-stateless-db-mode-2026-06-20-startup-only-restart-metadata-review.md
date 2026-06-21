# Startup-only Restart Metadata Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/persistence/runtime_config/sql.py`
- `backend/scripts/import_runtime_state_to_db.py`
- `backend/app/gateway/routers/config.py`
- `backend/app/gateway/app.py`
- `backend/tests/test_runtime_config_store.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `backend/tests/test_config_router.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: DB runtime config writes now report startup-only changes

The runtime config repository now compares the previous and new top-level app config payloads on update. Changed fields registered in `config.reload_boundary.STARTUP_ONLY_FIELDS` are returned as `restart_required_fields` with human-readable reasons, and `requires_restart` is derived from that metadata.

### P1 Fixed: Migration apply reports restart-required app config overwrites

When `import_runtime_state_to_db --apply --overwrite` replaces an existing DB app config row and the changed fields include startup-only entries such as `log_level` or `sandbox`, the JSON report now includes `applied.restart_required.app_config.fields` and `reasons`. This makes the migration report actionable without asking operators to inspect the raw config diff manually.

### P1 Fixed: Admin tooling can inspect the reload boundary through Gateway

`GET /api/config/reload-boundary` exposes the startup-only prefix and field reasons to admin users. Non-admin users receive the standard admin privilege failure. The endpoint is read-only and driven directly from the same reload-boundary registry used by schema descriptions and tests.

### P2 Remaining: This does not create a general app-config write API

The batch exposes restart-boundary metadata and reports restart impact for migration/apply writes. It does not add a generic DB app-config update endpoint. If such an endpoint is introduced later, it should return the same repository-level `restart_required_*` metadata from its write response.

## Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp -q
uv --directory backend run pytest tests/test_config_router.py -q
```

Result:

```text
Runtime config store: 2 failed, 9 passed, 1 warning in 0.23s
Import apply report: KeyError: 'restart_required'
Config router: ImportError: cannot import name 'config'
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp -q
uv --directory backend run pytest tests/test_config_router.py -q
```

Result:

```text
11 passed, 1 warning in 0.22s
1 passed, 1 warning in 0.35s
2 passed, 2 warnings in 0.47s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4798 passed, 36 skipped, 12 warnings in 83.84s
All checks passed!
git diff --check produced no output.
```

## Conclusion

The startup-only reload boundary is now exposed in three places that matter for stateless DB mode: DB config write results, migration apply reports, and admin-readable Gateway metadata. Remaining completion risks are still external live gates rather than this local reload-boundary proof.
