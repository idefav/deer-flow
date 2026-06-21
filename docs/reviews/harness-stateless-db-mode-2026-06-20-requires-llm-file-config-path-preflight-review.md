# Harness Stateless DB Mode Review: Requires LLM File Config Path Preflight

Date: 2026-06-20

## Scope

Review Batch 105 changes to the model-backed live-gate preflight:

- Make file-mode `requires_llm` preflight honor `DEER_FLOW_CONFIG_PATH`.
- Preserve the project-root `config.yaml` fallback when no explicit config path is set.
- Keep DB-mode `runtime_configs.app` behavior unchanged.

## Findings

### Closed: file-mode preflight now matches runtime config-path resolution

Runtime `AppConfig.resolve_config_path()` accepts `DEER_FLOW_CONFIG_PATH`, but `scripts/check_stateless_live_gates.py --gate requires_llm` previously loaded only `project_root/config.yaml`. That meant an operator could point the runtime at a valid model config and still get a false not-ready preflight result.

Batch 105 changes `_file_llm_config_readiness()` to prefer `DEER_FLOW_CONFIG_PATH` when present, report a path-specific `config_issues` message when that file is missing, and otherwise keep the existing project-root `config.yaml` fallback. The model payload checks and `$ENV_NAME` credential-reference scan remain unchanged.

Evidence:

- `backend/scripts/check_stateless_live_gates.py::_file_llm_config_readiness`
- `backend/tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_deer_flow_config_path`

## Residual Risks

- This proves config-source discovery and env-reference readiness. It does not validate provider credentials or model behavior.
- The `requires_llm` live gate still needs to run with a real configured model before production rollout.
- The remote provisioner/K8s live smoke remains a separate rollout gate.

## Verification

Focused checks run:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_deer_flow_config_path -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning
17 passed, 1 warning
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
4838 passed, 36 skipped, 12 warnings in 89.01s
All checks passed!
git diff --check produced no output.
```
