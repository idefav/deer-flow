# Review: Live Gate Evidence Required Log Validation

Date: 2026-06-21

## Scope

Reviewed Batch 121 changes that add `--require-logs` to `scripts/check_stateless_live_gates.py --validate-evidence`.

## Findings

- No blocking issues found in the implemented scope.
- Strict evidence validation can now require every execution entry to reference archived stdout/stderr logs.
- Validation rejects execution records that omit `stdout_log_path` or `stderr_log_path` when `--require-logs` is enabled.
- Validation also checks that referenced log files exist, closing the gap where JSON evidence could point to missing log artifacts.

## Residual Risk

- `--require-logs` validates referenced file presence in the current validation environment. If operators move an evidence bundle, they must preserve paths or validate from a location where the paths still resolve.
- The validation checks existence, not semantic completeness of pytest logs.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 1 failed, 1 warning in 0.23s
Focused require-logs test: 1 passed, 1 warning in 0.19s
Focused full preflight suite: 33 passed, 1 warning in 0.30s
Focused ruff: All checks passed!
Full pytest: 4862 passed, 36 skipped, 12 warnings in 89.95s
Full ruff: All checks passed!
git diff --check: clean
```
