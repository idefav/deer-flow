# Harness Stateless DB Mode Review: Sandbox Runtime Context Materialization

Date: 2026-06-19

## Scope

- `deerflow.sandbox.materializer`
- `deerflow.sandbox.tools`
- `deerflow.sandbox.middleware`
- sandbox materializer and middleware tests
- implementation log Batch 36

## Findings

### P1 Fixed: DB runtime context now has a manifest builder

`SandboxRuntimeContextManifestBuilder` builds a deterministic snapshot from DB-backed memory, agent, and skill stores. The manifest includes global user memory, agent-scoped memory, user profile, agent soul, `SKILL.md`, and skill support files.

### P1 Fixed: Lazy sandbox acquisition now materializes DB context

Both `ensure_sandbox_initialized()` and `ensure_sandbox_initialized_async()` call the materializer in DB config mode after acquiring or retrieving a sandbox. File mode skips this path and does not instantiate DB stores.

### P1 Fixed: Eager sandbox acquisition now materializes DB context

`SandboxMiddleware.before_agent()` and `SandboxMiddleware.abefore_agent()` now materialize after eager acquisition when the provider can return a sandbox instance. This closes the gap where eager mode acquired only a sandbox id.

### P1 Fixed: Warm sandbox stale files are pruned

The materializer now compares the previous `.manifest.json` file list with the new manifest and removes files absent from the new snapshot. This prevents old memory, agent, or skill files from leaking into a reused sandbox context directory.

### P2 Fixed In Batch 39 And Documented In Batch 58: Strict mode can fail closed

If manifest construction or sandbox writes fail, compatibility mode records `sandbox_context_error` and logs the exception while still returning the sandbox. Batch 39 added `SandboxConfig.runtime_context_fail_closed` so strict stateless deployments can raise `SandboxRuntimeError` instead. Batch 58 documents when to use each mode in the operator runbook.

Current behavior:

- compatibility mode keeps fail-open behavior.
- strict mode records `sandbox_context_error`, then fails closed.

### P2: Remote AIO is still covered by fake sandbox tests

The tests verify the shared `Sandbox` API contract, not a real container-backed AIO sandbox. This is enough for unit coverage, but not for the final remote stateless guarantee.

Recommendation:

- Add a remote/local-backend AIO smoke test that writes `/mnt/deerflow/context/.manifest.json` through the sandbox file API.
- Verify warm reuse prunes stale context files.

## Verification

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
uv --directory backend run pytest tests/test_sandbox_middleware.py -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py tests/test_sandbox_file_operation_tools.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py packages/harness/deerflow/sandbox/tools.py packages/harness/deerflow/sandbox/materializer.py packages/harness/deerflow/sandbox/middleware.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py
git diff --check
```

Results:

- `7 passed, 1 warning`
- `20 passed, 1 warning`
- `48 passed, 1 warning`
- `ruff check`: passed
- `git diff --check`: no output

## Conclusion

The runtime-context materializer is now connected to both lazy and eager sandbox lifecycle paths, and warm sandbox stale-file leakage is handled. The remaining risk is operational policy: strict stateless deployments should probably fail closed on materialization errors and still need a real AIO/container smoke test.
