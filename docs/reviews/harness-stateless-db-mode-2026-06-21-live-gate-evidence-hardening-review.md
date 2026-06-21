# Review: Live Gate Evidence Hardening

Date: 2026-06-21

## Scope

Reviewed the live-gate evidence hardening work across Batches 117-128:

- `--validate-evidence` shape and outcome validation.
- `--require-run` strict execution coverage.
- execution exit-code consistency checks.
- `--evidence-log-dir` stdout/stderr capture.
- `--require-logs` archived log presence checks.
- relative log path resolution from the evidence JSON directory.
- portable evidence bundle generation for relative `--evidence-log-dir` paths.
- strict execution command matching against the preflight gate command.
- stdout/stderr log SHA-256 and byte-count integrity validation.
- strict selected-gate validation for non-empty known gates, top-level ready preflight state, and per-gate `ready_to_invoke` state.
- strict execution-set validation for one execution record per selected gate and no unselected execution gates.
- strict gate-list uniqueness validation for `selected_gates` and selected entries in `preflight.gates`.

## Findings

- No blocking issues found in the reviewed scope.
- The evidence flow now distinguishes malformed evidence, structurally valid failed/skipped gates, and successful executed gates with separate exit-code semantics.
- Preflight-only evidence cannot satisfy rollout sign-off when `--require-run` is enabled.
- Evidence claiming `overall_exit_code: 0` is rejected if any recorded execution has a non-zero `exit_code`.
- Operators can archive command stdout/stderr beside JSON evidence, and strict validation can require those log artifacts to exist.
- Relative log paths are resolved from the evidence file directory, making archived bundles more portable than cwd-dependent validation.
- When `--evidence-path <bundle>/evidence.json --evidence-log-dir logs` is used, logs are now written under `<bundle>/logs/` while execution records keep `logs/...` paths, so generated bundles are portable by default.
- `--require-run` now verifies that each selected gate execution uses the exact preflight command for that gate, preventing a same-name execution record from standing in for a different command.
- `--require-logs` now verifies each referenced log file's SHA-256 and byte count, so archived logs cannot be silently replaced or truncated after evidence generation.
- `--require-run` now also rejects empty selected-gate lists, unknown gate names, not-ready top-level preflight evidence, and selected preflight gates whose `ready_to_invoke` state is not true, preventing forged evidence from satisfying rollout sign-off without a real supported gate.
- `--require-run` now rejects unselected execution gates and duplicate execution records, keeping strict evidence aligned with the generator's one-selected-gate-to-one-execution shape.
- `--require-run` now rejects duplicate selected gate names and duplicate selected preflight gate entries, preventing ambiguous command/readiness provenance in hand-authored evidence.

## Residual Risk

- The evidence validators prove archived record consistency and referenced log-file presence. They do not prove that the remote cluster or model provider remains healthy after the recorded run.
- Log validation checks file existence and content integrity, but not semantic completeness or correctness of pytest logs.
- Absolute log paths remain environment-specific; portable rollout bundles should prefer relative paths under the evidence directory.
- The two final external gates remain unexecuted in this workstation: remote provisioner/K8s smoke and real model-backed live tests.

## Verification Reviewed

```text
Focused live-gate evidence suite: 45 passed, 1 warning in 0.26s
Focused ruff: All checks passed!
Full pytest: 4874 passed, 36 skipped, 12 warnings in 89.52s
Full ruff: All checks passed!
git diff --check: clean
```
