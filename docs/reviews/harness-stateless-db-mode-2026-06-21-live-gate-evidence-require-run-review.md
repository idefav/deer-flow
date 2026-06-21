# Review: Live Gate Evidence Require-Run Validation

Date: 2026-06-21

## Scope

Reviewed Batch 118 changes that add `--require-run` to `scripts/check_stateless_live_gates.py --validate-evidence`.

## Findings

- No blocking issues found in the implemented scope.
- `--require-run` prevents preflight-only evidence from being treated as rollout sign-off, even when the archived `overall_exit_code` is `0`.
- Strict validation requires `run_requested: true` and one execution record for every selected gate.
- The default `--validate-evidence` behavior remains unchanged for callers that only want structural validation plus archived outcome status.

## Residual Risk

- `--require-run` proves the evidence records command execution coverage, not that the remote cluster or model provider remains healthy after the recorded run.
- The strict mode verifies that selected gates have execution records, but it still relies on `overall_exit_code` for the final archived outcome.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 2 failed, 1 warning in 0.27s
Focused require-run tests: 2 passed, 1 warning in 0.17s
Focused full preflight suite: 30 passed, 1 warning in 0.23s
Focused ruff: All checks passed!
Manual strict validation smoke: preflight-only evidence rejected with run_requested and missing execution errors
Full pytest: 4859 passed, 36 skipped, 12 warnings in 85.80s
Full ruff: All checks passed!
git diff --check: clean
```
