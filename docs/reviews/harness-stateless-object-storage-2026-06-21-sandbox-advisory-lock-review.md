# Harness Stateless Object Storage Review: Sandbox Advisory Lock

Date: 2026-06-21

## Scope

This batch removes the object-runtime sandbox creation dependency on local lock files under `.deer-flow`. Object mode now uses PostgreSQL advisory locking, which keeps sandbox creation coordination in the DB instead of the runtime PVC.

## Findings

- Object-mode sandbox creation no longer calls `get_paths().ensure_thread_dirs()` before acquiring a lock.
- `sandbox_advisory_lock_key()` creates stable positive 63-bit keys from `(thread_id, sandbox_id)`.
- `make_sandbox_creation_lock()` fails closed unless `runtime_storage.backend=object` and `database.backend=postgres`.
- `PostgresAdvisorySandboxLock` acquires `pg_advisory_lock()` and releases `pg_advisory_unlock()` using the configured PostgreSQL URL.
- Async object-mode sandbox creation is routed through the same advisory-lock creation path in a worker thread, keeping lock semantics consistent with the sync path.

## Verification

```bash
uv --directory backend run pytest tests/test_sandbox_advisory_lock.py tests/test_aio_sandbox_provider.py::test_discover_or_create_object_runtime_uses_advisory_lock_without_thread_dirs tests/test_aio_sandbox.py::TestDownloadFile tests/test_runtime_artifact_materializer.py tests/test_provisioner_pvc_volumes.py tests/test_uploads_router.py tests/test_artifacts_router.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py -q
uv --directory backend run ruff check packages/harness/deerflow/artifacts packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py app/gateway/routers/uploads.py app/gateway/routers/artifacts.py ../docker/provisioner/app.py tests/test_sandbox_advisory_lock.py tests/test_aio_sandbox_provider.py tests/test_aio_sandbox.py tests/test_runtime_artifact_materializer.py tests/test_provisioner_pvc_volumes.py tests/test_uploads_router.py tests/test_artifacts_router.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py
git diff --check
```

Result:

```text
111 passed, 2 warnings in 0.98s
All checks passed!
git diff --check produced no output.
```

## Remaining Risks

- Object mode requires a PostgreSQL database and the PostgreSQL driver in the runtime environment.
- The implementation creates a short-lived SQLAlchemy engine per advisory-lock context; this is acceptable for correctness but may be optimized later.
- Runtime PVC removal still needs migration tooling and target-cluster live evidence.
