# Harness Stateless Object Storage Review: Runtime Migration and Gate

Date: 2026-06-21

## Scope

This batch adds rollout tooling for object-backed runtime artifacts: filesystem/PVC import into object storage, static live-gate preflight, and operator documentation.

## Findings

- `import_runtime_artifacts_to_object_store.py` imports `workspace`, `uploads`, `outputs`, and `acp-workspace` files from user-isolated and legacy `.deer-flow` layouts.
- Legacy `threads/{thread_id}` artifacts import under owner `default`.
- Dry-run reports discovered files without writing object storage.
- `runtime_object_storage` live gate checks file/DB app config and provisioner env alignment.
- The gate fails closed unless backend config is `runtime_storage.backend=object`, `object_store.bucket` exists, `RUNTIME_STORAGE_BACKEND=object`, and `USERDATA_PVC_NAME` is unset.
- Operator runbook now includes SeaweedFS/MinIO/Ceph RGW guidance, migration commands, and static/live sign-off commands.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_artifacts_to_object_store.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_runtime_object_storage_gate_rejects_filesystem_runtime tests/test_stateless_live_gate_check.py::test_runtime_object_storage_gate_accepts_object_runtime -q
uv --directory backend run ruff check scripts/import_runtime_artifacts_to_object_store.py tests/test_import_runtime_artifacts_to_object_store.py scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
3 passed, 1 warning in 0.25s
2 passed, 1 warning in 0.26s
All checks passed!
```

## Remaining Risks

- The migration script is not a bidirectional export tool; rollback after object-mode writes needs an explicit operator decision.
- Static preflight cannot prove object-store credentials or network reachability; live target-environment evidence is still required.
- Remote live evidence must be collected with object runtime env and archived with logs before production sign-off.
