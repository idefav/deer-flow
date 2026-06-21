# Sandbox Materializer Foundation Review

Date: 2026-06-19

Reviewed change set:

- `deerflow.sandbox.materializer`
- materializer tests with a fake `Sandbox`

## Findings

### P1 Fixed: There is now a provider-agnostic context file materializer

`SandboxMaterializer` can write text and binary files into any sandbox implementation via the shared `Sandbox` interface. This gives local sandbox and AIO sandbox a common path for receiving DB-backed runtime context files.

Coverage added:

- text files.
- binary files.
- parent directory creation.
- manifest write.
- manifest-hash no-op behavior.

### P1 Fixed: Materialized paths are constrained to a safe context root

Manifest file paths must be relative and cannot contain `..`. The materializer writes under `/mnt/deerflow/context` by default and rejects unsafe paths before making sandbox calls.

### P1: Runtime snapshot builder and middleware wiring are still missing

This batch only implements the write mechanism. It does not yet build manifests from DB memory/agent/skill state and does not call the materializer from sandbox acquisition.

Required follow-up:

- Build a DB-backed manifest snapshot for memory, agent context, and active skills.
- Invoke materialization after eager and lazy sandbox acquisition.
- Store manifest hash/revision in runtime context for observability.

### P2: Remote AIO integration needs real API coverage

The tests use a fake `Sandbox`, so they verify interface behavior but not the actual `agent_sandbox` file API.

Required follow-up:

- Add a remote/AIO-style integration test using the existing `AioSandbox` fake/client stubs.

## Positive Checks

- Existing AIO and local sandbox mount tests remain green.
- Binary writes use `Sandbox.update_file()` rather than text encoding.
- Manifest writes happen last, so partial failures do not mark a snapshot complete.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py tests/test_aio_sandbox_provider.py tests/test_local_sandbox_provider_mounts.py -q
```

Result:

```text
75 passed, 1 warning in 0.91s
```

```bash
uv --directory backend run ruff check tests/test_sandbox_materializer.py packages/harness/deerflow/sandbox/materializer.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

## Review Conclusion

The file-writing foundation for sandbox stateless materialization is in place. The next work is DB snapshot construction and middleware/provider integration so actual Harness runs materialize the right files after sandbox acquisition.
