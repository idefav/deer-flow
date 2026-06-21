# Sandbox Runtime Context Revision Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/tests/test_sandbox_materializer.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P2 Fixed: Runtime-context revisions now change when materialized content changes

Before Batch 89, `SandboxRuntimeContextManifestBuilder` revisions encoded user, agent, and requested skill names, but not the actual DB-backed runtime context contents. Manifest hashes still detected content changes during materialization, but the builder-level revision was weaker than the design's runtime snapshot intent.

Batch 89 adds a deterministic file-content snapshot hash to the revision string. The revision still includes the readable user/agent/skills labels, and it now changes when memory, agent, SOUL, or skill file content changes.

### P2 Guarded: Manifest hash no-op behavior remains intact

The lower-level `SandboxMaterializer` still computes the canonical manifest hash from revision plus file records and skips writes when the manifest hash matches. Existing no-op, prune, strict-mode, and DB-context materialization tests still pass.

### P2 Guarded: Revision hashing is deterministic

The content hash is built from sorted file records using the same normalized path, size, binary flag, and sha256 content metadata used by materializer manifests. File order therefore does not affect the revision.

### P2 Remaining: Remote K8s live proof is still external

This batch strengthens the local snapshot revision contract. It does not execute the remote provisioner/K8s smoke; that remains an operator rollout gate because unit tests cannot prove deployed hostPath/PVC permissions.

## Verification

Red check:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_revision_changes_when_content_changes -q
```

Result:

```text
1 failed, 1 warning in 0.32s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_revision_changes_when_content_changes -q
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
uv --directory backend run ruff check packages/harness/deerflow/sandbox/materializer.py tests/test_sandbox_materializer.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
1 passed, 1 warning in 0.26s
11 passed, 1 warning in 0.34s
All checks passed!
4808 passed, 36 skipped, 12 warnings in 84.44s
All checks passed!
git diff --check produced no output.
```

## Conclusion

Sandbox runtime-context manifests now expose a content-sensitive snapshot revision in addition to the existing manifest hash, closing the local revision-contract gap while leaving remote live proof as the remaining environment gate.
