# Requires LLM Live Gate Config Preflight Review

Date: 2026-06-20

## Scope

- `scripts/check_stateless_live_gates.py`
- `tests/test_stateless_live_gate_check.py`
- operator runbook live-gate preflight instructions
- implementation log Batch 95

## Findings

### P2 Fixed: `requires_llm` no longer reports ready without model config

Batch 94's preflight treated `requires_llm` as ready when no required environment variables were missing. On this workstation, `config.yaml` exists but has no configured models, so `tests/test_client_live.py` would skip while the preflight said `ok: true`.

Batch 95 adds model-config checks for `requires_llm`. The report now includes `config_issues`, and the gate is not ready when `config.yaml` is missing, malformed, non-object YAML, has no configured models, has a non-list `models` value, or has no minimally valid model entries.

### P2 Fixed: Rollout automation can distinguish missing env from missing config

The preflight now reports three separate readiness classes:

- `missing_env`
- `invalid_env`
- `config_issues`

This lets rollout scripts route remote/provisioner environment problems separately from model config problems.

### P2 Remaining: Provider credentials still require live execution

The preflight proves that at least one model is declared. It does not call the provider, validate API keys, or prove model behavior. The `requires_llm` live gate still needs to run in an environment with real credentials.

## Verification

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_requires_config_yaml_with_models tests/test_stateless_live_gate_check.py::test_requires_llm_gate_rejects_empty_models_config tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_config_with_models -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `3 failed, 1 warning`
- targeted LLM config checks: `3 passed, 1 warning`
- stateless live-gate preflight suite: `7 passed, 1 warning`
- focused aggregate with marker guard and remote live entrypoint: `10 passed, 1 skipped, 1 warning`
- full backend regression: `4820 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- default workstation `requires_llm` preflight: exit code `2` with `config.yaml has no configured models`
- `git diff --check`: clean

## Conclusion

The live-gate preflight no longer gives false confidence for model-backed gates in no-model environments. Actual provider behavior remains an external live-gate requirement.
