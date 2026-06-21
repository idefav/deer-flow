# Harness Stateless DB Mode Review: Requires LLM DB Live Client Entrypoint

Date: 2026-06-20

## Scope

Review Batch 104 changes to the model-backed live client test entrypoint:

- Make `tests/test_client_live.py` honor `DEER_FLOW_CONFIG_SOURCE=db`.
- Share bootstrap DB AppConfig loading between gateway startup and the direct live-client test entrypoint.
- Delay embedded-client imports until after DB AppConfig preload so collection does not emit pre-preload config errors.
- Preserve file-mode skip behavior on CI, missing `config.yaml`, and empty model config.

## Findings

### Closed: direct live-client pytest no longer bypasses DB config preload

Batches 95-97 made the `requires_llm` preflight CLI DB-aware, but direct execution of `tests/test_client_live.py` still called `get_app_config()` before `load_and_cache_db_app_config()` had run. In DB mode that produced a module-level skip even when `runtime_configs.app` had a valid model entry.

Batch 104 adds a DB-mode branch to the live-test readiness helper. It calls `load_and_cache_bootstrap_db_app_config()`, the same lower-level bootstrap loader used by gateway startup, which initializes the bootstrap DB engine, loads DB extensions, caches the DB AppConfig, and then lets the embedded `DeerFlowClient` path read `get_app_config()` normally.

The batch also delays `DeerFlowClient` / `StreamEvent` imports until after module-level readiness. This prevents collection from importing prompt/skill-cache code before DB config is preloaded.

Evidence:

- `backend/packages/harness/deerflow/config/app_config.py::load_and_cache_bootstrap_db_app_config`
- `backend/app/gateway/app.py::_load_startup_config`
- `backend/tests/test_client_live.py`
- `backend/tests/test_client_live_db_mode_readiness.py::test_client_live_entrypoint_uses_db_config_readiness`

## Residual Risks

- This proves direct pytest collection/import is aligned with DB config source. It does not call a real model provider or validate credential correctness.
- `requires_llm` behavior still needs a real file-mode or DB-mode model configuration plus provider credentials before production rollout.
- The remote provisioner/K8s live smoke remains a separate rollout gate.

## Verification

Focused checks run:

```bash
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py tests/test_gateway_db_config_startup.py -q
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_client_live.py -q
uv --directory backend run ruff check packages/harness/deerflow/config/app_config.py app/gateway/app.py tests/test_client_live.py tests/test_client_live_db_mode_readiness.py tests/test_gateway_db_config_startup.py
uv --directory backend run ruff check tests/test_client_live.py tests/test_client_live_db_mode_readiness.py tests/test_stateless_live_gate_check.py
```

Result:

```text
3 passed, 1 warning
17 passed, 1 warning
19 skipped, 1 warning
All checks passed!
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
4837 passed, 36 skipped, 12 warnings in 89.08s
All checks passed!
git diff --check produced no output.
```
