# Harness Stateless Object Storage Review: Close-Out Audit

Date: 2026-06-21

## Scope

This review records the final local close-out state after implementing object-backed runtime storage and pushing `feature/upgrade`.

## Findings

- Runtime PVC removal is implemented behind `runtime_storage.backend=object`.
- Runtime workspace, uploads, outputs, and ACP workspace files are backed by S3-compatible object storage in object mode.
- AIO sandbox lifecycle materializes object-store files into sandbox paths and flushes them back before release.
- The bundled provisioner omits `/mnt/user-data` in object mode and fails closed when `USERDATA_PVC_NAME` is still configured.
- Gateway uploads/artifacts, middleware, built-in tools, IM channel file ingestion/attachment handling, Feishu file downloads, and the embedded client have object-mode paths.
- Existing `.deer-flow`/PVC runtime artifacts can be imported with `scripts/import_runtime_artifacts_to_object_store.py`.
- The `runtime_object_storage` gate provides static readiness checks, but it is not a substitute for target-environment evidence.
- The latest pushed commit is `b1dea0c6 feat: add object storage runtime mode`.

## Verification

```bash
uv --directory backend run pytest tests/blocking_io/test_channels_ingest.py::test_ingest_inbound_files_does_not_block_event_loop -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
git status --short --branch
git ls-remote origin refs/heads/feature/upgrade
```

Result:

```text
1 passed, 1 warning in 0.70s
4936 passed, 36 skipped, 12 warnings in 91.84s
All checks passed!
git diff --check produced no output.
## feature/upgrade...origin/feature/upgrade
b1dea0c6ff26d3a99e2ace557eb25834f66ac8b8 refs/heads/feature/upgrade
```

## Remaining Risks

- Production sign-off still requires live evidence from the target deployment.
- Static preflight cannot prove object-store network reachability, credentials, bucket policy, or provisioner/K8s runtime behavior.
- The required evidence bundle must include `runtime_object_storage`, `remote_live`, and `requires_llm` readiness/run records as applicable, plus logs validated with `--require-run --require-logs`.
