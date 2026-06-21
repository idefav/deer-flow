# Harness Stateless Object Storage Review: Gateway Object Mode

Date: 2026-06-21

## Scope

This batch wires the gateway upload and artifact routers to `ArtifactStore` when `runtime_storage.backend=object`.

## Findings

- Uploads in object mode write directly to object storage and do not create thread upload directories.
- Object-mode upload listing and deletion read from the scoped `ArtifactStore` prefix.
- Artifact preview/download in object mode reads bytes from `ArtifactStore` and preserves active-content forced-download behavior.
- `.skill` archive preview can now extract from object-store bytes as well as local files.
- Filesystem mode remains the default and existing router tests continue to pass.

## Verification

```bash
uv --directory backend run pytest tests/test_uploads_router.py tests/test_artifacts_router.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py -q
uv --directory backend run ruff check app/gateway/routers/uploads.py app/gateway/routers/artifacts.py packages/harness/deerflow/artifacts tests/test_uploads_router.py tests/test_artifacts_router.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py
git diff --check
```

Result:

```text
63 passed, 2 warnings in 0.73s
All checks passed!
git diff --check produced no output.
```

## Remaining Risks

- Sandbox pods still need materialize/flush wiring before runtime PVC removal is complete.
- Tools and middleware that inspect local thread folders still need object-mode handling.
- Provisioner YAML/env handling still permits runtime PVC mounts.
- No live SeaweedFS/S3 evidence has been captured yet.
