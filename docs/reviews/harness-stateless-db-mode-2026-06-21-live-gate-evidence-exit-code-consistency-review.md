# Review: Live Gate Evidence Exit-Code Consistency

Date: 2026-06-21

## Scope

Reviewed Batch 119 changes that make `scripts/check_stateless_live_gates.py --validate-evidence` compare recorded execution exit codes with a successful root `overall_exit_code`.

## Findings

- No blocking issues found in the implemented scope.
- Evidence validation now rejects contradictory rollout records where `overall_exit_code` is `0` but any recorded execution has a non-zero `exit_code`.
- The new coverage exercises the rollout sign-off path with `--require-run`, `run_requested: true`, an execution record for the selected gate, and a failing per-command exit code.
- Failed archived gate outcomes remain valid evidence when the root `overall_exit_code` is non-zero; they still return the gate outcome status instead of being treated as malformed.

## Residual Risk

- This validation proves consistency inside the archived JSON evidence. It still cannot prove external services remain reachable after the recorded run.
- Operators should continue to keep pytest stdout/stderr logs beside the JSON evidence for troubleshooting details beyond command exit codes.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 1 failed, 1 warning in 0.21s
Focused contradictory-evidence test: 1 passed, 1 warning in 0.18s
Focused full preflight suite: 31 passed, 1 warning in 0.24s
Focused ruff: All checks passed!
Full pytest: 4860 passed, 36 skipped, 12 warnings in 88.70s
Full ruff: All checks passed!
git diff --check: clean
```
