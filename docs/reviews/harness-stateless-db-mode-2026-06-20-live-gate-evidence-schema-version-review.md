# Review: Live Gate Evidence Schema Version

Date: 2026-06-20

## Scope

Reviewed Batch 115 changes that add a root-level `schema_version` field to `scripts/check_stateless_live_gates.py --evidence-path` JSON reports.

## Findings

- No blocking issues found in the implemented scope.
- Evidence reports now include `schema_version: 1`, giving rollout tooling an explicit compatibility marker for archived remote/model gate records.
- Coverage asserts the field for both preflight-only evidence and `--run` evidence, so both operator paths stay aligned.
- The change is additive. It does not alter gate readiness checks, selected pytest commands, execution behavior, source metadata, or exit-code semantics.

## Residual Risk

- `schema_version` only versions the evidence-report JSON shape. It does not prove external prerequisites such as reachable provisioner services, writable Kubernetes node paths, Docker daemon availability, or valid model provider credentials.
- This batch still does not execute the remote provisioner/K8s smoke or real model-backed live tests.

## Verification Reviewed

```text
Red: 1 failed, 1 warning in 0.19s
Focused evidence tests: 2 passed, 1 warning in 0.20s
Focused full preflight suite: 23 passed, 1 warning in 0.26s
Focused ruff: All checks passed!
Manual evidence smoke: schema_version=1, selected_gates=["requires_llm"], overall_exit_code=2
Full pytest: 4852 passed, 36 skipped, 12 warnings in 86.95s
Full ruff: All checks passed!
git diff --check: no output
```
