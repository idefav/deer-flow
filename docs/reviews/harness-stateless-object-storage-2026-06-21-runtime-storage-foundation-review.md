# Harness Stateless Object Storage Review: Runtime Storage Foundation

Date: 2026-06-21

## Scope

This batch creates the foundation for replacing the runtime PVC with object storage. It adds a typed `runtime_storage` config section and a runtime artifact store contract keyed by sandbox-visible paths.

## Findings

- No production PVC removal is claimed in this batch. Existing filesystem/PVC behavior remains the default through `runtime_storage.backend=filesystem`.
- Object mode now has a fail-fast config shape: `runtime_storage.backend=object` requires an `object_store` block.
- Object keys are deterministic and user/thread scoped: `deerflow/users/{user_id}/threads/{thread_id}/...`.
- Path validation rejects traversal and paths outside `/mnt/user-data` and `/mnt/acp-workspace`.
- `runtime_storage` is registered as startup-only because backend switching requires rebuilding the sandbox/provider materialization path.

## Verification

```bash
uv --directory backend run pytest tests/test_runtime_storage_config.py tests/test_runtime_artifact_store.py -q
uv --directory backend run pytest tests/test_runtime_storage_config.py tests/test_runtime_artifact_store.py tests/test_reload_boundary.py -q
uv --directory backend run ruff check packages/harness/deerflow/config/runtime_storage_config.py packages/harness/deerflow/config/app_config.py packages/harness/deerflow/config/__init__.py packages/harness/deerflow/config/reload_boundary.py packages/harness/deerflow/artifacts tests/test_runtime_storage_config.py tests/test_runtime_artifact_store.py
git diff --check
```

Result:

```text
11 passed, 1 warning in 0.24s
21 passed, 1 warning in 0.21s
All checks passed!
git diff --check produced no output.
```

## Remaining Risks

- No S3/SeaweedFS client exists yet; `InMemoryArtifactStore` is only a focused test implementation.
- Gateway upload/download and preview paths still read/write local files.
- Sandbox provisioning still mounts runtime user data through existing filesystem/PVC wiring.
- Migration and live gate evidence for object-backed runtime files are still pending.
