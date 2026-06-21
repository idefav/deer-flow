# Live AIO Prompt Skill Path Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/packages/harness/deerflow/sandbox/tools.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/packages/harness/deerflow/config/paths.py`
- `backend/tests/test_sandbox_materializer.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `backend/tests/test_aio_sandbox_live.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`

## Findings

### P1 Fixed: DB skill files were materialized to a path different from the prompt-exposed path

Batch 65 proved DB skill support files could be read from storage APIs and written through the AIO file API, but the default materializer target put `skills/...` under `/tmp/deerflow/context/skills/...`. The prompt and skill tooling expose `skills.container_path`, defaulting to `/mnt/skills`, so a remote/container sandbox could still show the model a path that did not contain the DB skill files.

Batch 67 adds `skills_root` routing to `SandboxMaterializer` and passes `config.skills.container_path` from runtime DB materialization. Skill entries now land at `/mnt/skills/...` by default while memory/agent context and the manifest remain under `/tmp/deerflow/context`.

### P1 Fixed: Local Docker AIO needed a writable `skills.container_path`

The upgraded live smoke exposed a real AIO permission issue:

```text
OSError: [Errno 13] Permission denied: '/mnt/skills'
```

The root cause is that the AIO shell/file API user cannot create `/mnt/skills` under the image's root-owned `/mnt`. Batch 67 adds a per-thread writable scratch directory and mounts it to `skills.container_path` in DB mode for local Docker AIO. The old read-only skill-source mount is skipped in DB mode so DB materialization can write prompt-visible skill files through the sandbox file API.

### P2 Remaining: Remote provisioner/K8s must provide the same writable-path contract

The local Docker path is now covered by unit tests and an opt-in live smoke. Remote provisioner/K8s backends still need either:

- a writable volume/emptyDir mounted at `skills.container_path` with permissions for the AIO shell/file API user, or
- a configured `skills.container_path` that is already writable inside the sandbox, such as `/tmp/deerflow/skills`.

This is now documented as an operator rollout gate rather than hidden as a generic live-AIO gap.

## Verification

Commands run:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_get_thread_mounts_can_include_writable_db_skills_dir tests/test_aio_sandbox_provider.py::test_get_skills_mount_uses_active_skill_storage_root -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_get_extra_mounts_uses_writable_db_skills_mount -q
DEER_FLOW_RUN_LIVE_AIO_SANDBOX=1 uv --directory backend run pytest tests/test_aio_sandbox_live.py -q --tb=short
uv --directory backend run pytest tests/test_sandbox_materializer.py tests/test_aio_sandbox_provider.py tests/test_aio_sandbox.py tests/test_aio_sandbox_live.py -q
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_sandbox_materializer.py tests/test_aio_sandbox_provider.py tests/test_aio_sandbox.py tests/test_runtime_lifecycle_e2e.py::test_db_mode_stream_run_completes_without_local_runtime_config_files tests/test_gateway_db_config_startup.py -q
uv --directory backend run ruff check packages/harness/deerflow/config/paths.py packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py packages/harness/deerflow/sandbox/materializer.py packages/harness/deerflow/sandbox/tools.py tests/test_sandbox_materializer.py tests/test_aio_sandbox_provider.py tests/test_aio_sandbox_live.py
git diff --check
```

Results:

```text
10 passed, 1 warning in 0.36s
2 passed, 1 warning in 0.18s
1 passed, 1 warning in 0.18s
1 passed, 1 warning in 8.06s
67 passed, 1 skipped, 1 warning in 0.89s
82 passed, 2 warnings in 1.82s
All checks passed!
git diff --check produced no output.
```

## Conclusion

The local Docker AIO materialization requirement is now meaningfully verified: DB skill files are delivered to the same path the model sees in prompts. The remaining risk is deployment-specific remote provisioner/K8s volume setup, not the core materializer or local Docker AIO file API path.
