# Harness Stateless Object Storage Review: S3 Artifact Store

Date: 2026-06-21

## Scope

This batch adds a production S3-compatible ArtifactStore implementation and factory wiring from `RuntimeStorageConfig`. It is intended for SeaweedFS or another S3-compatible open-source object store.

## Findings

- Object mode now has a concrete storage backend: `S3ArtifactStore`.
- The S3 client is lazy-created, so filesystem mode and focused unit tests do not require immediate object-store connectivity.
- The store keeps the object key layout introduced in Batch 113: `deerflow/users/{user_id}/threads/{thread_id}/...`.
- Upload metadata preserves user fields and adds `deerflow-sha256` internally for integrity/list metadata.
- `boto3` is now a harness dependency and is present in `backend/uv.lock`.

## Verification

```bash
uv --directory backend run pytest tests/test_runtime_storage_config.py tests/test_runtime_artifact_store.py tests/test_reload_boundary.py -q
uv --directory backend run ruff check packages/harness/deerflow/artifacts packages/harness/deerflow/config/runtime_storage_config.py packages/harness/deerflow/config/app_config.py packages/harness/deerflow/config/reload_boundary.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py
git diff --check
```

Result:

```text
24 passed, 1 warning in 0.23s
All checks passed!
git diff --check produced no output.
```

## Remaining Risks

- Gateway and tool flows still use local files until they are wired through `ArtifactStore`.
- The provisioner still creates runtime PVC/hostPath mounts.
- There is no live SeaweedFS/S3 evidence yet.
- Object-store migration from existing `.deer-flow` or runtime PVC files is still pending.
