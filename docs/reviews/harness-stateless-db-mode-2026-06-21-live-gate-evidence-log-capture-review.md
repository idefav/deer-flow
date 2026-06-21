# Review: Live Gate Evidence Log Capture

Date: 2026-06-21

## Scope

Reviewed Batch 120 changes that add `--evidence-log-dir` to `scripts/check_stateless_live_gates.py` so live-gate runs can archive command stdout/stderr beside JSON evidence.

## Findings

- No blocking issues found in the implemented scope.
- `--evidence-log-dir` records per-command stdout/stderr files and stores their paths in each execution entry.
- The default no-log path keeps the existing runner behavior, so normal live pytest output remains unchanged unless operators explicitly request archived logs.
- The focused test covers the operator-facing CLI path with `--run`, `--evidence-path`, and a ready gate.

## Residual Risk

- The evidence JSON stores log file paths, not embedded log contents. Operators still need to archive the referenced log directory with the evidence JSON.
- Captured default subprocess output is written after each command exits; it is optimized for archival evidence, not live streaming.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 1 failed, 1 warning in 0.21s
Focused log-capture test: 1 passed, 1 warning in 0.22s
Focused full preflight suite: 32 passed, 1 warning in 0.31s
Focused ruff: All checks passed!
Full pytest: 4861 passed, 36 skipped, 12 warnings in 89.45s
Full ruff: All checks passed!
git diff --check: clean
```
