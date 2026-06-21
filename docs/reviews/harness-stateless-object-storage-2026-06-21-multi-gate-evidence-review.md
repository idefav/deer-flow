# Harness Stateless Object Storage Review: Multi-Gate Evidence Bundle

Date: 2026-06-21

## Scope

This review covers the rollout evidence guidance for runtime PVC removal. The live-gate CLI supports repeated `--gate` arguments, so production sign-off should generate one portable evidence bundle that covers `runtime_object_storage`, `remote_live`, and `requires_llm`.

## Findings

- A single `--evidence-path <bundle>/evidence.json` can include multiple selected gates.
- Each selected gate gets one preflight entry and one execution record when `--run` is used.
- Relative `--evidence-log-dir logs` writes logs under `<bundle>/logs/` and records portable `logs/...` paths in the evidence JSON.
- `--validate-evidence --require-run --require-logs` already enforces one execution per selected gate, command matching, and log integrity.
- The runbook now avoids running separate gate commands against the same evidence path, which would make the final JSON ambiguous or overwrite earlier evidence.

## Verification

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_single_evidence_bundle_for_multiple_gates -q
```

Result:

```text
1 passed, 1 warning in 0.31s
```

## Remaining Risks

- The test proves evidence bundling behavior with a fake runner; it does not replace target-environment execution.
- Production sign-off still requires valid object storage config, provisioner reachability, remote sandbox execution, and real model credentials.
