# Sandbox Context Skill Storage API Review

Date: 2026-06-20

Reviewed change set:

- `SandboxRuntimeContextManifestBuilder._iter_skill_files()`
- DB skill support-file manifest coverage with absent host cache
- `AioSandbox` file API contract for text and binary context writes

## Findings

### P1 Fixed: Sandbox context no longer depends on DB skill support files being materialized on host

Before this batch, sandbox runtime-context skill collection used `skill.skill_dir.rglob("*")`. After Batch 63, DB custom skill listing intentionally materializes only `SKILL.md` into the host cache. That meant DB-backed support files such as `references/notes.md` and binary assets could be absent from the sandbox context when the host cache was missing or freshly pruned.

Batch 65 changes the builder to read through the storage API:

- `list_skill_file_manifest(skill.name, skill.category)`
- `read_skill_file(skill.name, skill.category, item.relative_path)`

That makes DB storage the source of truth for sandbox context files and keeps local filesystem storage compatible through the base storage implementation.

Regression coverage:

- write a DB custom skill.
- write `references/notes.md` and `assets/logo.bin`.
- delete the materialized host skill cache.
- build the sandbox runtime-context manifest.
- assert `SKILL.md`, the text support file, and the binary asset are all present.

### P1 Fixed: AIO text and binary file writes have a contract test

The new `AioSandbox` contract test verifies the materializer uses:

- text writes via `file.write_file(file=..., content=...)`.
- binary writes via `file.write_file(file=..., content=<base64>, encoding="base64")`.

This gives coverage for the remote sandbox file API shape without requiring Docker/K8s in unit tests.

### P2 Remaining: No live remote sandbox smoke yet

This batch proves the builder and `AioSandbox` client contract. It does not start a real Docker container, provisioner, or Kubernetes sandbox. A live smoke remains useful before production rollout if the deployment depends on remote file API availability or host-path translation.

## Verification

Red test:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_reads_db_memory_agent_and_skill -q
```

Result:

```text
FAILED ... KeyError: 'skills/custom/research/references/notes.md'
```

Green targeted tests:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_reads_db_memory_agent_and_skill -q
uv --directory backend run pytest tests/test_aio_sandbox.py::TestFileOperations::test_materializer_writes_context_files_through_aio_file_api -q
```

Result:

```text
1 passed, 1 warning in 0.27s
1 passed, 1 warning in 0.18s
```

## Conclusion

The sandbox runtime-context path now uses the skill storage abstraction instead of host-cache traversal. DB-backed support files and binary skill assets can be delivered into sandbox context even when the host cache starts empty. The remaining evidence gap is a live AIO/container smoke, not the DB-to-manifest code path.
