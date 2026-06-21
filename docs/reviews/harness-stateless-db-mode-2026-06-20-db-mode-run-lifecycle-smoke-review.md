# DB-Mode Run Lifecycle Smoke Review

Date: 2026-06-20

Reviewed change set:

- DB-mode runtime lifecycle E2E fixture
- DB-seeded `runtime_configs.app` / `runtime_configs.extensions`
- local runtime config files intentionally absent
- streaming run lifecycle through the real Gateway stack

## Findings

### P1 Fixed: DB-mode startup/run path now has focused E2E evidence

Before this batch, DB-mode startup had focused config preload tests, and run lifecycle had file-mode E2E coverage, but there was no single test proving that the Gateway could start from DB bootstrap config and complete a normal run while local runtime config files were absent.

Batch 64 adds `test_db_mode_stream_run_completes_without_local_runtime_config_files`, which covers:

- FastAPI lifespan.
- `_load_startup_config()` DB branch.
- `DEER_FLOW_CONFIG_SOURCE=db`.
- `DEER_FLOW_DATABASE_URL`.
- DB-backed app and extensions config payloads.
- auth registration.
- `/api/threads` creation.
- `/api/threads/{thread_id}/runs/stream`.
- `start_run()`, `run_agent()`, StreamBridge, checkpointer, run store, and thread metadata readback.

The test points `DEER_FLOW_CONFIG_PATH` and `DEER_FLOW_EXTENSIONS_CONFIG_PATH` to missing files and asserts those files remain absent, so accidental file-mode fallback would fail loudly.

### P1 Fixed: The smoke avoids external LLM dependencies

The test reuses the existing runtime lifecycle fake agent factory. This keeps the run path real while making the model result deterministic and network-free.

### P2 Remaining: This is not remote AIO/container proof

The smoke uses the local sandbox provider and a fake agent. It proves DB-mode Gateway startup and run lifecycle wiring, not remote AIO materialization or Docker host-path behavior. That remains a separate production evidence gap.

### P2 Remaining: Runtime files are absent, not a full read-only filesystem simulation

The test proves no `config.yaml` or `extensions_config.json` path is required by the DB-mode startup/run path. It does not make every legacy runtime directory read-only. A stricter read-only volume smoke can be added if the deployment target requires it.

## Verification

Initial failure:

```bash
uv --directory backend run pytest tests/test_runtime_lifecycle_e2e.py::test_db_mode_stream_run_completes_without_local_runtime_config_files -q
```

Result:

```text
ERROR ... sqlite3.OperationalError: unable to open database file
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_runtime_lifecycle_e2e.py::test_db_mode_stream_run_completes_without_local_runtime_config_files -q
```

Result:

```text
1 passed, 2 warnings in 0.78s
```

## Conclusion

The DB-mode Gateway startup/thread/run smoke gap is closed for a local, deterministic E2E path. The overall plan still needs remote/container AIO materialization evidence and final broad verification before completion can be claimed.
