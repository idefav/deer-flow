# Remote Provisioner Anonymous Thread Payload Review

Date: 2026-06-20

## Scope

- `packages/harness/deerflow/community/aio_sandbox/remote_backend.py`
- `tests/test_remote_sandbox_backend.py`
- bundled provisioner `CreateSandboxRequest` compatibility
- implementation log Batch 93

## Findings

### P2 Fixed: Anonymous remote sandbox creates no longer send `thread_id: null`

`AioSandboxProvider.acquire()` supports anonymous sandbox acquisition with `thread_id=None`. The remote backend test suite also covered anonymous create calls, but `RemoteSandboxBackend` sent `thread_id: null` to the provisioner.

The bundled provisioner request model requires `thread_id` to be a string matching `SAFE_THREAD_ID_PATTERN`. In provisioner/K8s mode, an anonymous remote sandbox create would therefore fail validation before Pod creation.

Batch 93 changes the provisioner payload to send `thread_id or sandbox_id`, so anonymous creates use the already-safe sandbox id as the provisioner thread key.

### P2 Fixed: Existing anonymous-create coverage now matches the provisioner contract

The existing anonymous-create regression test now expects `thread_id` to be the sandbox id instead of `None`. That keeps the backend-level compatibility guard aligned with the bundled provisioner model instead of testing a payload the provisioner cannot accept.

### P2 Remaining: Real remote runtime still requires live smoke

This batch closes a request-model compatibility issue. It does not replace the remote provisioner/K8s smoke that proves node path/PVC permissions and AIO file API write access.

## Verification

```bash
uv --directory backend run pytest tests/test_remote_sandbox_backend.py::test_provisioner_create_accepts_anonymous_thread_id -q
uv --directory backend run pytest tests/test_remote_sandbox_backend.py -q
uv --directory backend run ruff check packages/harness/deerflow/community/aio_sandbox/remote_backend.py tests/test_remote_sandbox_backend.py
uv --directory backend run pytest tests/test_remote_sandbox_backend.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `1 failed, 1 warning`
- targeted anonymous payload compatibility test: `1 passed, 1 warning`
- remote backend regression set: `23 passed, 1 warning`
- remote backend plus remote-live entrypoint aggregate: `23 passed, 1 skipped, 1 warning`
- full backend regression: `4812 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: clean

## Conclusion

The remote backend now sends a provisioner-compatible `thread_id` for anonymous sandbox creation while preserving normal thread-specific sandbox behavior.
