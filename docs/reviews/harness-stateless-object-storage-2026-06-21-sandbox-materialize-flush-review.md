# Harness Stateless Object Storage Review: Sandbox Materialize and Flush

Date: 2026-06-21

## Scope

This batch wires object-backed runtime files into AIO sandbox lifecycle. It removes `/mnt/user-data` and `/mnt/acp-workspace` host/PVC mounts in object mode and synchronizes files over the sandbox file API instead.

## Findings

- `SandboxArtifactMaterializer` materializes workspace/uploads/outputs/ACP files from `ArtifactStore` into sandbox paths.
- `SandboxArtifactMaterializer.flush_thread()` uploads sandbox changes back to `ArtifactStore`.
- `AioSandboxProvider._get_thread_mounts()` returns no user-data or ACP host mounts when `runtime_storage.backend=object`.
- Sandbox registration paths materialize object files after create/discover/reclaim.
- `release()` flushes thread artifacts before closing the sandbox client.
- `AioSandbox.download_file()` now allows `/mnt/acp-workspace` in addition to `/mnt/user-data`, with traversal protection unchanged.

## Verification

```bash
uv --directory backend run pytest tests/test_aio_sandbox.py::TestDownloadFile tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox_provider.py::test_get_thread_mounts_object_runtime_skips_user_data_and_acp_mounts tests/test_aio_sandbox_provider.py::test_create_sandbox_materializes_object_runtime_after_ready tests/test_aio_sandbox_provider.py::test_release_flushes_object_runtime_before_closing_sandbox -q
uv --directory backend run ruff check packages/harness/deerflow/artifacts packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox.py tests/test_aio_sandbox_provider.py
git diff --check
```

Result:

```text
16 passed, 1 warning in 0.27s
All checks passed!
git diff --check produced no output.
```

## Remaining Risks

- Object-mode sandbox creation still uses the legacy file lock path; DB advisory lock is still needed for complete PVC removal.
- Provisioner strict validation still needs to block runtime PVC env/config.
- Some tools and prompt middleware still inspect local thread folders and need object-mode alternatives.
- No live SeaweedFS/S3 evidence has been captured yet.
