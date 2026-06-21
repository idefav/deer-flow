# Review: Live Gate Evidence Report Output

Date: 2026-06-20

## Scope

Reviewed Batch 111 changes that add `--evidence-path` to `scripts/check_stateless_live_gates.py` and document how operators should archive live-gate preflight/run evidence.

## Findings

- No blocking issues found in the implemented scope.
- The feature is additive: existing stdout text/JSON behavior and `--run` semantics are preserved.
- Evidence files include the authoritative preflight report, selected gate names, `run_requested`, executed pytest commands with exit codes, and overall exit code.
- Not-ready gates write evidence without invoking the runner, preserving the existing fail-fast behavior.
- The run evidence test uses a ready `docker_live` gate with an injected runner, so it verifies execution recording without starting Docker.

## Residual Risk

- The evidence file records command exit codes, not external system state beyond what the tests print. Operators should keep the pytest stdout/stderr logs beside the JSON evidence for full troubleshooting context.
- This batch does not execute the remote provisioner/K8s smoke or real model-backed live tests; it only improves the evidence capture path for those external gates.

## Verification Reviewed

```text
Red: 2 failed, 1 warning in 0.23s
Focused new tests: 2 passed, 1 warning in 0.17s
Focused full preflight suite: 23 passed, 1 warning in 0.18s
Focused ruff: All checks passed!
Manual evidence smoke: selected_gates=["requires_llm"], run_requested=false, executions=[], overall_exit_code=2
Full pytest: 4852 passed, 36 skipped, 12 warnings in 85.81s
Full ruff: All checks passed!
git diff --check: no output
```
