# Harness Stateless DB Mode Review: Requires LLM Direct Entrypoint Readiness

Date: 2026-06-20

## Scope

- Align direct real-LLM pytest entrypoints with the shared `requires_llm` live-gate readiness contract.
- Keep provider-neutral file/DB model configs runnable even when `OPENAI_API_KEY` is not the configured credential env var.
- Preserve unconfigured-workstation behavior for non-LLM `test_client_e2e.py` checks.

## Findings

### P2 Fixed: Direct real-LLM entrypoints still used OpenAI-specific readiness

Batches 95-106 made the preflight CLI understand file mode, `DEER_FLOW_CONFIG_PATH`, DB mode, DB bootstrap URL parsing, and `$ENV_NAME` model credential references. Two direct pytest entrypoints still had local rules:

- `test_client_e2e.py` skipped real-LLM tests when `OPENAI_API_KEY` was unset.
- `test_create_deerflow_agent_live.py` skipped on the same env var and created `ChatOpenAI` directly from E2E-specific env vars.

That meant a valid active config such as `api_key: $AZURE_OPENAI_API_KEY` could be preflight-ready but still skipped or bypassed during direct pytest execution.

Batch 107 adds `tests/support/live_gate_readiness.py`, reuses the preflight `requires_llm` report for skip decisions, and routes model creation through active AppConfig/model-factory paths.

### P2 Fixed: Marker regression guard needed to understand the shared helper

After replacing local `pytest.mark.*` expressions with `mark_requires_llm`, the static marker guard needed to recognize that helper as applying both `live` and `requires_llm`. Batch 107 updates `test_live_gate_markers.py` so the helper does not become a blind spot in future live-gate coverage checks.

## Design Notes

- `test_client_e2e.py` still uses its legacy synthetic config when `requires_llm` readiness is not satisfied. This keeps file upload/config-management E2E tests useful on machines without configured models.
- When live readiness is satisfied, `test_client_e2e.py` switches to the active AppConfig and overrides only the sandbox provider for test isolation.
- `test_deferred_tool_promotion_real_llm.py` remains intentionally OneAPI-specific because it exercises that gateway flow behind `ONEAPI_E2E=1`.

## Remaining Risk

- This batch proves provider-neutral readiness and wiring locally; it does not prove provider credentials are valid or that a configured remote model can complete a request. The `requires_llm` live gate still needs to run with real credentials.
- Remote provisioner/K8s smoke remains a separate external rollout gate.

## Verification

```bash
uv --directory backend run pytest tests/test_requires_llm_live_entrypoint_readiness.py tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run pytest -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

```text
40 passed, 14 skipped, 1 warning in 1.66s
5 passed, 1 warning in 0.80s
34/66 tests collected (32 deselected) in 0.42s
34 skipped, 32 deselected, 1 warning in 0.44s
4844 passed, 36 skipped, 12 warnings in 85.48s
All checks passed!
git diff --check produced no output.
```
