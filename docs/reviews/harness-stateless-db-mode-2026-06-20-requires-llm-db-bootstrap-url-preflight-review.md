# Harness Stateless DB Mode Review: Requires LLM DB Bootstrap URL Preflight

Date: 2026-06-20

## Scope

Review Batch 106 changes to the model-backed DB-mode live-gate preflight:

- Reuse runtime bootstrap DB URL parsing for `requires_llm` DB-mode preflight.
- Preserve sqlite's standard `{sqlite_dir}/deerflow.db` runtime path convention.
- Keep file-mode `DEER_FLOW_CONFIG_PATH` and DB model/env-reference checks unchanged.

## Findings

### Closed: DB-mode preflight now inspects the same sqlite DB file as runtime startup

Gateway DB startup derives a `DatabaseConfig` from `DEER_FLOW_DATABASE_URL`. For sqlite URLs, that parser treats the URL path parent as `sqlite_dir` and the actual app DB path as `{sqlite_dir}/deerflow.db`. The preflight script previously converted the env URL directly into a sync SQLAlchemy URL, which could inspect `bootstrap-placeholder.db` while runtime would open `deerflow.db`.

Batch 106 changes `_sync_sqlalchemy_url()` to call `database_config_from_url()` and then derive the sync URL from `DatabaseConfig.app_sqlalchemy_url`. This keeps sqlite and Postgres preflight DB selection aligned with gateway startup semantics.

Evidence:

- `backend/scripts/check_stateless_live_gates.py::_sync_sqlalchemy_url`
- `backend/tests/test_stateless_live_gate_check.py::test_requires_llm_gate_uses_bootstrap_sqlite_database_path`

## Residual Risks

- This proves DB selection and model/env-reference readiness. It does not validate provider credentials or model behavior.
- The `requires_llm` live gate still needs to run with a real configured model before production rollout.
- The remote provisioner/K8s live smoke remains a separate rollout gate.

## Verification

Focused checks run:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_uses_bootstrap_sqlite_database_path -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning
18 passed, 1 warning
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
4839 passed, 36 skipped, 12 warnings in 85.01s
All checks passed!
git diff --check produced no output.
```
