# Harness Stateless DB Mode Review: Channel Runtime DB Store

Date: 2026-06-20

## Scope

Review Batch 101 changes for UI-entered IM channel runtime credentials:

- DB-backed `DbChannelRuntimeConfigStore` using `runtime_configs.channel_runtime`.
- Source-aware `get_channel_runtime_config_store()` factory.
- Gateway router offloading for runtime store construction and DB-backed reads.
- Runtime-state import apply/conflict handling for channel runtime config.
- Documentation updates for technical design, operator runbook, requirement audit, and implementation log.

## Findings

### Closed: DB mode no longer falls back to local channel runtime JSON for new writes

`get_channel_runtime_config_store()` returns `DbChannelRuntimeConfigStore` when `DEER_FLOW_CONFIG_SOURCE=db` or when a DB config is supplied explicitly. The DB store persists the existing provider-keyed payload shape into `runtime_configs.channel_runtime`, bumps revision/content hash, and leaves file mode on `ChannelRuntimeConfigStore`.

Evidence:

- `backend/app/channels/runtime_config_store.py`
- `backend/tests/blocking_io/test_channel_runtime_config_store.py::test_runtime_config_store_factory_uses_db_in_db_config_mode`

### Closed: Gateway runtime paths stay event-loop safe

The router now treats runtime channel stores through `ChannelRuntimeConfigStoreProtocol`, constructs the selected store through `asyncio.to_thread`, and offloads `apply_runtime_connection_config()` / `merge_runtime_channel_configs()` so DB reads do not run on the event loop.

Evidence:

- `backend/app/gateway/routers/channel_connections.py`
- `backend/tests/blocking_io/test_channel_runtime_config_store.py`
- `backend/tests/test_channel_connections_router.py`

### Closed: Migration apply imports channel runtime config

Dry-run already discovered `.deer-flow/channels/runtime-config.json`; Batch 101 now also writes it into `runtime_configs.channel_runtime` during apply and reports existing target rows as `channel_runtime_config` conflicts unless overwrite is enabled.

Evidence:

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_channel_runtime_config`
- `backend/tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_channel_runtime_conflict`

## Residual Risks

- The DB store uses the existing `runtime_configs` aggregate-row pattern, so concurrent multi-process writes to different channel providers can still race like other aggregate runtime-config writes. This is acceptable for the current V1 parity scope but should be revisited if channel credential edits become high-frequency or multi-admin concurrent workflows.
- Existing deployed DBs only get the `channel_runtime` row after migration apply or after the next UI write. Operators must check `applied.channel_runtime_config` when migrating from file mode.
- Remote/K8s live smoke and real-model live gates are still external rollout gates and are not closed by this batch.

## Verification

Focused checks run:

```bash
uv --directory backend run pytest tests/blocking_io/test_channel_runtime_config_store.py -q
uv --directory backend run pytest tests/test_channel_connections_router.py -q
uv --directory backend run pytest tests/test_channels.py -k "from_app_config" -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check app/channels/runtime_config_store.py app/gateway/routers/channel_connections.py tests/blocking_io/test_channel_runtime_config_store.py
```

Result:

```text
7 passed, 1 warning
30 passed, 2 warnings
5 passed, 208 deselected, 1 warning
28 passed, 1 warning
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4832 passed, 36 skipped, 12 warnings in 86.22s
All checks passed!
git diff --check produced no output.
```
