# Review: Live Gate Evidence Relative Log Path Validation

Date: 2026-06-21

## Scope

Reviewed Batch 122 changes that make `scripts/check_stateless_live_gates.py --validate-evidence --require-logs` resolve relative log paths from the evidence file directory.

## Findings

- No blocking issues found in the implemented scope.
- Relative `stdout_log_path` and `stderr_log_path` values now validate against the evidence JSON parent directory, making copied or unpacked evidence bundles easier to verify.
- Absolute log paths keep their previous behavior.
- The focused regression test exercises validation from a cwd outside the evidence bundle, which proves the path base is no longer the process cwd.

## Residual Risk

- This still validates file presence rather than pytest log content semantics.
- Evidence bundles that contain absolute log paths remain tied to those absolute paths; operators should prefer relative paths when creating portable archives.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 1 failed, 1 warning in 0.21s
Focused relative-log test: 1 passed, 1 warning in 0.18s
Focused full preflight suite: 34 passed, 1 warning in 0.27s
Focused ruff: All checks passed!
Full pytest: 4863 passed, 36 skipped, 12 warnings in 89.60s
Full ruff: All checks passed!
git diff --check: clean
```
