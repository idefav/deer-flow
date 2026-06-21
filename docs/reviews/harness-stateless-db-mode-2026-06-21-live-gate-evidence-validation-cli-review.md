# Review: Live Gate Evidence Validation CLI

Date: 2026-06-21

## Scope

Reviewed Batch 117 changes that add `scripts/check_stateless_live_gates.py --validate-evidence <file>`.

## Findings

- No blocking issues found in the implemented scope.
- `--validate-evidence` returns `0` only when the evidence JSON is structurally valid and records `overall_exit_code: 0`.
- Valid evidence that records a failed or skipped gate returns `2`, preserving the existing preflight/run failure convention.
- Malformed evidence, unsupported `schema_version`, invalid JSON, unreadable files, or missing required root fields return `1`.
- The validator does not re-run gates and does not inspect credential values. It validates the archived report shape and archived outcome only.

## Residual Risk

- Evidence validation cannot prove that external services are still reachable after the gate ran; it only validates the archived evidence file.
- The schema check is intentionally focused on root evidence fields and archived outcome. It is not a deep JSON Schema validator for every nested preflight field.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 3 failed, 1 warning in 0.33s; 1 failed, 1 warning in 0.22s
Focused validation tests: 4 passed, 1 warning in 0.20s
Focused full preflight suite: 28 passed, 1 warning in 0.28s
Focused ruff: All checks passed!
Manual validation smoke: valid=true, success=false, schema_version=1, selected_gates=["requires_llm"], overall_exit_code=2
Full pytest: 4857 passed, 36 skipped, 12 warnings in 91.24s
Full ruff: All checks passed!
git diff --check: no output
```
