# Review: Live Gate Evidence Source Metadata

Date: 2026-06-20

## Scope

Reviewed Batch 113 changes that add git source metadata to `scripts/check_stateless_live_gates.py --evidence-path` output.

## Findings

- No blocking issues found in the implemented scope.
- Evidence files now include `source.git` with repository root, HEAD, branch, dirty flag, and status-line count when git metadata is available.
- The git lookup is best-effort. If git is unavailable or the command is run outside a repository, evidence generation records `available: false` with an error instead of failing the preflight.
- The feature is additive and does not alter gate readiness checks, selected pytest commands, or exit-code behavior.

## Residual Risk

- `status_short_count` records only the number of dirty status lines, not the file list. This avoids bloating the evidence file but means operators should still archive the exact deployed artifact or git diff when they need full source reconstruction.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 1 failed, 1 warning in 0.19s
Focused new tests: 2 passed, 1 warning in 0.23s
Focused full preflight suite: 23 passed, 1 warning in 0.24s
Focused ruff: All checks passed!
Manual evidence smoke: git available=true, head present=true, branch=feature/upgrade, dirty=true
Full pytest: 4852 passed, 36 skipped, 12 warnings in 85.80s
Full ruff: All checks passed!
git diff --check: no output
```
