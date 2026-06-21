# Requires LLM DB Config Preflight Review

Date: 2026-06-20

## Scope

- `scripts/check_stateless_live_gates.py`
- `tests/test_stateless_live_gate_check.py`
- operator runbook live-gate preflight instructions
- implementation log Batch 96

## Findings

### P2 Fixed: `requires_llm` preflight now honors DB config source

Batch 95 made `requires_llm` check file-mode `config.yaml`, but DB/stateless deployments use `runtime_configs.app` as the app config source of truth. In `DEER_FLOW_CONFIG_SOURCE=db`, a valid DB-backed model configuration could still fail preflight because there was no local `config.yaml`.

Batch 96 makes the preflight source-aware. When `DEER_FLOW_CONFIG_SOURCE=db`, it requires `DEER_FLOW_DATABASE_URL`, loads `runtime_configs.app`, and validates the DB payload's `models` list. File mode still uses `config.yaml`.

### P2 Fixed: DB bootstrap mistakes are reported as config issues

The DB-mode preflight now reports missing `DEER_FLOW_DATABASE_URL`, missing `runtime_configs.app`, unreadable DB rows, and invalid model payloads through `config_issues`, keeping them distinct from remote provisioner environment problems.

### P2 Remaining: Model provider behavior still requires live execution

The preflight proves that a model entry is declared in the active config source. It does not call the provider or validate API credentials. The `requires_llm` live gate remains the behavior proof.

## Verification

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_db_config_with_models tests/test_stateless_live_gate_check.py::test_requires_llm_gate_db_mode_requires_database_url -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `2 failed, 1 warning`
- targeted DB config-source checks: `2 passed, 1 warning`
- stateless live-gate preflight suite: `9 passed, 1 warning`
- focused aggregate with marker guard and remote live entrypoint: `12 passed, 1 skipped, 1 warning`
- default workstation `requires_llm` preflight: exit code `2` with `config.yaml has no configured models`
- full backend regression: `4822 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: clean

## Conclusion

The live-gate preflight now follows the selected app config source for model-backed gates, so stateless DB deployments are not incorrectly judged by file-mode `config.yaml` rules.
