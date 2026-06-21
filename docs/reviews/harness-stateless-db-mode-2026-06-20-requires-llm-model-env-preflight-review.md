# Requires LLM Model Env Preflight Review

Date: 2026-06-20

## Scope

- `scripts/check_stateless_live_gates.py`
- `tests/test_stateless_live_gate_check.py`
- operator runbook live-gate preflight instructions
- implementation log Batch 97

## Findings

### P2 Fixed: `requires_llm` preflight now catches missing model env references

The file and DB config-source checks previously proved that at least one model entry existed, but they did not validate `$ENV_NAME` references inside the model payload. A config such as `api_key: $OPENAI_API_KEY` could therefore be reported ready even when `OPENAI_API_KEY` was absent.

Batch 97 adds shared model-payload readiness for file and DB mode. Once the model payload is structurally valid, the preflight recursively scans configured model entries for strings that start with `$`, matching `AppConfig.resolve_env_variables()`, and reports absent or empty variables through `missing_env`.

### P2 Fixed: Positive readiness tests now include credentials explicitly

The positive file-mode and DB-mode model-config tests now pass `OPENAI_API_KEY`, so a green preflight means both a usable model declaration and the config-declared credential env reference are present.

### P2 Remaining: Provider-side validity still requires live execution

The preflight can detect that env variables referenced by config exist. It still cannot prove the credential is valid, that the provider endpoint is reachable, or that the selected model can complete a request. The `requires_llm` live gate remains the behavior proof.

## Verification

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_missing_file_model_env_reference tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_nested_file_model_env_reference tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_missing_db_model_env_reference -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `2 failed, 1 warning`
- targeted file/DB/nested missing-env checks: `3 passed, 1 warning`
- stateless live-gate preflight suite: `12 passed, 1 warning`
- focused aggregate with marker guard and remote live entrypoint: `15 passed, 1 skipped, 1 warning`
- full backend regression: `4825 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: clean

## Conclusion

The live-gate preflight now treats config-declared model credential env references as first-class readiness inputs for both file-backed and DB-backed app config.
