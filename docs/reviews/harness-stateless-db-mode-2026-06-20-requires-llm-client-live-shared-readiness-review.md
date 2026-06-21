# Harness Stateless DB Mode Review: Requires LLM Client Live Shared Readiness

Date: 2026-06-20

## Scope

- Align `tests/test_client_live.py` with the shared `requires_llm` live-gate readiness helper.
- Preserve DB-mode active AppConfig preload for direct pytest execution.
- Keep live-gate marker regression detection precise after the shared helper rollout.

## Findings

### P2 Fixed: `test_client_live.py` still had a local readiness implementation

Batch 104 made the direct live-client entrypoint preload DB AppConfig before deciding whether to skip. Batches 105-107 then moved the preflight and other real-LLM entrypoints to one shared readiness contract. `test_client_live.py` still had its own `config.yaml`/DB branching, so future changes to `scripts/check_stateless_live_gates.py --gate requires_llm` could drift away from direct pytest behavior again.

Batch 108 changes `test_client_live.py` to:

- call `support.live_gate_readiness.requires_llm_skip_reason()` for the module-level skip reason;
- call `load_active_app_config_for_requires_llm()` when readiness passes;
- keep DB-mode bootstrap AppConfig loading through the same shared loader used by other direct real-LLM entrypoints.

### P2 Fixed: Marker guard matched helper names too broadly

The marker regression guard treated the substring `_requires_llm_skip` as a real-LLM signal. The new `requires_llm_skip_reason` helper contains that substring but is not itself a live-gate marker. Batch 108 tightens the detector to AST exact-name matching for the legacy `_requires_llm_skip` variable and adds a negative guard test.

## Remaining Risk

- This batch unifies local readiness decisions. It still does not prove provider credentials are valid or that a configured remote model can complete a request.
- Remote provisioner/K8s smoke remains a separate external rollout gate.

## Verification

```bash
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py -q
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py tests/test_requires_llm_live_entrypoint_readiness.py tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_client_live.py tests/test_live_gate_markers.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run pytest -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
uv --directory backend run ruff check tests/test_client_live.py tests/test_client_live_db_mode_readiness.py tests/test_live_gate_markers.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

```text
3 passed, 1 warning in 0.21s
6 passed, 1 warning in 1.19s
44 passed, 33 skipped, 1 warning in 2.12s
34/66 tests collected (32 deselected) in 0.51s
34 skipped, 32 deselected, 1 warning in 0.49s
preflight exit code 2 with config_issues=["config.yaml has no configured models"]
All checks passed!
4847 passed, 36 skipped, 12 warnings in 85.86s
All checks passed!
git diff --check produced no output.
```
