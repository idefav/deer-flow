# Harness Stateless Object Storage Runtime Local-State Close-Out Fix Review

Date: 2026-06-21

## Scope

This review covers the repair batch that closes the remaining local runtime storage gaps after introducing `runtime_storage.backend=object`.

## Issues Addressed

- `SandboxArtifactMaterializer.flush_thread()` uploaded sandbox files but did not delete stale object-store files, so deleted sandbox files could reappear on the next materialize.
- `ObjectStoreConfig.max_materialize_files` and `max_materialize_bytes` existed but were not enforced before materializing runtime files into a sandbox.
- `AioSandboxProvider.release()` flushed object runtime files, but destroy, shutdown, unhealthy-drop, idle warm-pool destroy, warm-pool eviction, and shutdown warm-pool destroy paths could skip flush.
- Local-container object mode still reported `uses_thread_data_mounts=True`, which could send large tool-output externalization to gateway-local outputs instead of sandbox/object storage.
- ACP agents still used persistent `.deer-flow/.../acp-workspace` as their working directory in object mode.
- Gateway and embedded-client object uploads stored original files but skipped document-to-markdown companion conversion.

## Result

- Runtime materialization now fails closed when object-store file count or total bytes exceed configured budgets.
- Runtime flush now reconciles deletions by treating the sandbox filesystem as authoritative for each runtime root.
- AIO sandbox lifecycle paths now attempt best-effort object runtime flush before closing or destroying tracked sandboxes.
- Warm-pool entries released from active sandboxes retain associated thread IDs so later eviction/shutdown can flush with a temporary AIO client; orphan-adopted warm-pool entries remain compatible two-tuples because no thread ID is known.
- Object-mode local-container providers now report no thread-data mounts, preserving the sandbox/object-store path for large tool-output files.
- ACP object mode stages `/mnt/acp-workspace` in a temporary directory, materializes existing object-store ACP files before invocation, and reconciles created, modified, and deleted files back after invocation.
- Object-mode gateway and embedded-client uploads now write markdown companion files to object storage with the same response fields as filesystem mode.

## Verification

```bash
uv --directory backend run pytest tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox_provider.py tests/test_tool_output_budget_middleware.py tests/test_invoke_acp_agent_tool.py -q
uv --directory backend run pytest tests/test_uploads_router.py tests/test_client.py tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_sandbox_orphan_reconciliation.py::test_reconcile_adopts_young_containers tests/test_aio_sandbox_provider.py -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
158 passed, 1 warning in 0.76s
237 passed, 2 warnings in 1.24s
41 passed, 1 warning in 0.43s
4950 passed, 36 skipped, 12 warnings in 91.28s
All checks passed!
git diff --check produced no output.
```

## Remaining Risk

- Production sign-off still requires live evidence from a target object-mode deployment using `runtime_object_storage`, `remote_live`, and `requires_llm` gates.
- This batch does not change the object store provider contract; it still assumes an S3-compatible backend and does not add cross-object transactionality.
- ACP subprocesses still require a local temporary working directory during invocation, but that directory is staging only and is removed after reconcile.
