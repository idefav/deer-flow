# Review: Live Gate Source Metadata Test Portability

Date: 2026-06-20

## Scope

Reviewed Batch 114 changes that make the live-gate evidence source metadata test independent of the real checkout's `.git` directory.

## Findings

- No blocking issues found in the implemented scope.
- The test now injects deterministic `_git_metadata()` output and asserts the evidence report preserves that exact `source.git` payload.
- The change is test-only for runtime behavior. It does not alter live-gate preflight readiness, evidence schema, command selection, or exit-code behavior.
- The portability concern from Batch 113 is closed: the focused evidence test no longer fails merely because CI runs from a source package or copied tree without git metadata.

## Residual Risk

- The production `_git_metadata()` fallback path is still best-effort and intentionally records `available: false` with an error when git is unavailable or the command is outside a repository.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Focused preflight suite: 23 passed, 1 warning in 0.24s
Focused ruff: All checks passed!
Full pytest: 4852 passed, 36 skipped, 12 warnings in 86.58s
Full ruff: All checks passed!
git diff --check: no output
```
