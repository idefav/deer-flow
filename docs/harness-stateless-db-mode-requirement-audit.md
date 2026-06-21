# Harness Stateless DB Mode Requirement Audit

Date: 2026-06-20

This audit compares the current worktree against:

- `docs/harness-stateless-db-mode-plan.md`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-v1-scope-decisions.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- review documents under `docs/reviews/`

Status legend:

- **Verified**: current code has focused implementation evidence and tests.
- **Partial**: core behavior exists, but scope or proof is narrower than the plan.
- **Evidence Gap**: implementation likely exists, but current evidence does not prove the full requirement.
- **Design Divergence**: implementation intentionally or accidentally differs from the technical design.
- **Deferred V1 Boundary**: not implemented as a V1 product commitment and must stay documented.

## Executive Summary

The worktree has implemented the main DB-backed runtime state foundations:

- DB-backed app config source and bootstrap startup path.
- DB-backed extensions, MCP rows, skill enabled state, cache revision invalidation, and masked secret round-trip.
- DB-backed custom agents, user profiles, default-agent SOUL, and agent management API paths.
- DB-backed memory storage with revision freshness and updater retry handling.
- DB-backed custom skill storage with binary/mode preservation and router/tool integration.
- Runtime state import dry-run/apply/overwrite paths for config, extensions/MCP, agents, memory, custom skills, and default-agent SOUL.
- Runtime state import dry-run reports app config, channel runtime, and MCP env references, resolved-secret risks, stdio MCP stateless risks, and source schema validation errors before apply.
- Sandbox runtime-context materialization for memory, agent/profile/SOUL, and skills, including strict fail-closed mode.
- Operator runbook, per-batch implementation trail, and consolidated review trail through Batch 128.
- Embedded Python client MCP/skill update APIs honor DB mode instead of falling back to `extensions_config.json`.
- DB/stateless mode Memory storage now fails closed instead of silently falling back to local file storage.
- Runtime-state import preflight errors include stable machine-readable codes while preserving human-readable messages.
- Runtime-state import CLI success paths keep stderr clean while stdout remains the JSON report channel.
- Sandbox runtime-context revisions include deterministic content snapshots, not only user/agent/skill names.
- Runtime-state import skill rows now import public and custom skills into DB-backed normalized skill storage.
- Bundled provisioner idempotent create now rejects existing Pods whose mount contract differs from the current request.
- Remote provisioner create failures now preserve provisioner response bodies in gateway-side RuntimeError details.
- Anonymous remote sandbox creates now send the provisioner a string `thread_id` fallback based on `sandbox_id`.
- Remaining live gates now have a machine-checkable preflight CLI that reports missing environment and exact pytest commands before execution.
- The `requires_llm` live-gate preflight now reports missing or empty model config as `config_issues` instead of returning false-ready.
- The `requires_llm` live-gate preflight is now config-source aware: file mode checks `DEER_FLOW_CONFIG_PATH` when set, then `DEER_FLOW_PROJECT_ROOT/config.yaml` when configured, then the default project-root `config.yaml`; DB mode checks `runtime_configs.app` through the same bootstrap DB URL parsing used by gateway startup.
- The direct `tests/test_client_live.py` entrypoint now preloads DB-backed AppConfig through the shared bootstrap DB loader before its module-level `requires_llm` skip decision.
- The direct real-LLM E2E entrypoints now share the same `requires_llm` readiness helper as preflight and use the active file/DB AppConfig instead of hard-coding `OPENAI_API_KEY` / hand-built OpenAI-compatible model settings.
- The direct `tests/test_client_live.py` entrypoint now also reuses the shared `requires_llm` readiness helper before preloading active file/DB AppConfig, keeping direct pytest execution and the preflight CLI on the same readiness contract.
- The original implementation plan checklist is now aligned with the current V1 audit state instead of showing completed tasks as unchecked.
- Live-gate preflight can now write and strictly validate versioned JSON evidence reports with generation time, cwd, git source metadata, checked env names, non-empty known unique selected gates, exactly one ready preflight entry per selected gate, exactly one execution per selected gate, no unselected execution gates, per-command exit codes, execution-command matching against preflight commands, portable per-command stdout/stderr log paths, stdout/stderr SHA-256 and byte-count integrity metadata, required-log validation, and overall exit-code consistency for rollout sign-off; the source-metadata evidence test no longer depends on the test checkout containing `.git`, and multi-gate evidence bundles are covered by regression tests so runtime-object, remote, and model gates can be archived together without overwriting the JSON evidence file.
- UI-entered IM channel runtime credentials now honor DB mode at runtime through `runtime_configs.channel_runtime`; file mode remains backed by `.deer-flow/channels/runtime-config.json`.
- Runtime-state import apply now writes channel runtime config into `runtime_configs.channel_runtime` and reports existing `channel_runtime` rows as preflight conflicts unless `--overwrite` is used.
- The `remote_live` live-gate preflight now rejects malformed provisioner URLs, non-absolute host/container paths, and invalid ready timeouts before invoking pytest.
- The direct `tests/test_aio_sandbox_remote_live.py` entrypoint now validates the same remote-live env shape before creating a sandbox.
- Runtime PVC removal is implemented behind `runtime_storage.backend=object`: runtime workspace/uploads/outputs/ACP files use S3-compatible object storage, AIO sandboxes materialize/flush through the file API, the bundled provisioner omits `/mnt/user-data`, object-mode sandbox creation uses PostgreSQL advisory locks, and `runtime_object_storage` preflight fails closed unless backend config and provisioner env are aligned.
- A runtime artifact migration tool imports existing `.deer-flow`/PVC files into object storage before production cutover.

The overall goal should **not** be marked complete yet. The remaining gaps are concentrated in proof and a few implementation boundaries:

1. **Runtime PVC removal is implemented, but object-mode live evidence is still required.** The code now supports object-backed runtime artifacts, migration from existing `.deer-flow`/PVC files, sandbox materialize/flush, gateway/client/channel/tool object reads/writes, provisioner PVC removal, and static `runtime_object_storage` preflight. The remote smoke still must be run in a real deployment with `RUNTIME_STORAGE_BACKEND=object`, no `USERDATA_PVC_NAME`, configured object storage, and archived evidence to prove cluster/provisioner behavior and end-to-end runtime artifact flow.
2. **Public skills and custom skills are now DB-backed through normalized skill tables.** `skills` stores category/name/metadata/revision and `skill_files` stores `SKILL.md`, support files, binary payloads, file mode, hash, and size. DB mode no longer reads public skills from the gateway filesystem; missing public seed/import data fails closed.
3. **Skill progressive loading is retained.** Skill listing reads normalized metadata and materializes only `SKILL.md` as the prompt/cache compatibility path; support files and binary assets are read on demand through `read_skill_file()` / `list_skill_file_manifest()`.
4. **Final backend verification is current with live-client auto-skip in unconfigured environments.** `uv --directory backend run pytest -q` passes in this workstation with model-backed live tests skipped because local `config.yaml` has no configured models. Batch 95 makes the live-gate preflight report that no-model state as `config_issues` for `requires_llm`; Batch 96 makes the same preflight respect DB config source when `DEER_FLOW_CONFIG_SOURCE=db`; Batch 97 reports missing `$ENV_NAME` model credential references as `missing_env`; Batch 104 makes the direct `tests/test_client_live.py` entrypoint preload DB AppConfig through the same lower-level bootstrap loader used by gateway startup before deciding whether to skip; Batch 105 makes file-mode preflight honor `DEER_FLOW_CONFIG_PATH`, matching `AppConfig.resolve_config_path()`; Batch 106 makes DB-mode preflight use the same bootstrap sqlite/Postgres URL parser as runtime startup; Batch 107 makes `test_client_e2e.py` and `test_create_deerflow_agent_live.py` use the shared readiness helper and active AppConfig; Batch 108 makes `test_client_live.py` use the same helper before preloading active config; Batch 109 makes file-mode preflight honor `DEER_FLOW_PROJECT_ROOT/config.yaml` when no explicit config path is set. Current full regression: `4936 passed, 36 skipped, 12 warnings`. `uv --directory backend run ruff check .` passes, and `git diff --check` is clean.

## Acceptance Criteria Audit

| Acceptance criterion | Status | Current evidence | Remaining action |
| --- | --- | --- | --- |
| DB mode starts from bootstrap DB info without local writable runtime files | **Verified by focused E2E / Remote live-smoke execution gate** | `config/bootstrap.py`, `load_and_cache_db_app_config()`, gateway startup tests, DB-backed stores, sandbox materializer tests, Batch 64 `test_db_mode_stream_run_completes_without_local_runtime_config_files`, Batch 70 provisioner `extra_mounts` forwarding coverage, Batch 72 bundled provisioner consumption coverage, Batch 74 opt-in remote smoke test entrypoint, Batch 79 startup-only restart metadata for DB config writes/migration/admin inspection, Batch 80 operator runbook guidance for consuming that metadata, Batch 94 live-gate preflight CLI command/env checks, Batch 101 DB-backed channel runtime store coverage, Batch 102 remote-live env shape validation, Batch 103 direct remote-live entrypoint validation, Batch 111 evidence-file output for live-gate preflight/run records, Batch 112 evidence metadata for generation time/cwd, Batch 113 git source metadata, Batch 114 source-metadata test portability, Batch 115 evidence schema versioning, Batch 116 checked-env evidence coverage, Batch 117 evidence validation CLI, Batch 118 require-run evidence validation, Batch 119 execution exit-code consistency validation, Batch 120 execution log capture, Batch 121 required-log evidence validation, Batch 122 relative log path validation, Batch 123 portable evidence bundle generation, Batch 124 strict execution-command matching, Batch 125 log integrity validation, Batch 126 selected-gate/readiness strictness, Batch 127 execution-set strictness, Batch 128 gate-list uniqueness, and Batch 135 multi-gate evidence coverage | Run `scripts/check_stateless_live_gates.py --gate runtime_object_storage --gate remote_live --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in a real target environment before production rollout, then validate the archived evidence with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`. |
| Runtime PVC is removed for sandbox workspace/uploads/outputs/ACP files | **Verified by focused coverage / Static object-storage gate / Remote live-smoke execution gate** | `RuntimeStorageConfig`, `S3ArtifactStore`, gateway upload/artifact object-mode tests, sandbox materializer tests, AIO provider object-mode mount/advisory-lock tests, provisioner object-runtime gate tests, uploads/thread-data/present/view-image middleware/tool object tests, channel manager/Feishu object file tests, embedded client object tests, runtime artifact import tests, `runtime_object_storage` live-gate preflight, and multi-gate evidence bundle coverage | Run `scripts/import_runtime_artifacts_to_object_store.py`, then run `scripts/check_stateless_live_gates.py --gate runtime_object_storage --gate remote_live --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in the target environment and validate it with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`. |
| File mode remains backward-compatible | **Verified by focused coverage** | File source tests, existing router/tool tests, file-mode default-agent SOUL API tests | Keep in final regression suite. |
| Existing Gateway API semantics remain stable | **Verified by broad backend suite / Live client gate** | Agent, skill, MCP, memory focused router tests; default-agent SOUL API added with file/DB parity; embedded Python client MCP/skill update APIs now have DB-mode parity; full backend suite passes with `4936 passed, 36 skipped, 12 warnings` in the current no-model environment; Batches 75-77 mark live gates so operators can collect/run them with `pytest -m live`, `pytest -m requires_llm`, `pytest -m docker_live`, or `pytest -m remote_live`; Batch 78 adds a regression guard so future opt-in live tests fail fast when expected markers are missing; Batch 79 adds read-only admin config metadata at `/api/config/reload-boundary`; Batch 81 adds embedded-client DB extensions write coverage; Batch 83 guards source-neutral OpenAPI wording for MCP/skill runtime mutation endpoints; Batch 95 prevents `requires_llm` preflight from reporting ready when `config.yaml` has no configured models; Batch 96 makes that check honor DB-backed `runtime_configs.app` in DB mode; Batch 97 reports missing `$ENV_NAME` references inside file/DB model config as `missing_env`; Batch 101 keeps channel runtime configure/disconnect router coverage green while adding DB mode store selection; Batch 104 aligns direct `test_client_live.py` DB-mode skip/readiness behavior with the shared bootstrap DB preload path; Batch 105 aligns file-mode preflight with `DEER_FLOW_CONFIG_PATH`; Batch 106 aligns DB-mode preflight with bootstrap DB URL parsing; Batch 107 aligns `test_client_e2e.py` and `test_create_deerflow_agent_live.py` with the shared readiness helper and active AppConfig; Batch 108 makes `test_client_live.py` use that same readiness helper for all config sources before active-config preload; Batch 109 aligns file-mode preflight with `DEER_FLOW_PROJECT_ROOT/config.yaml`; Batch 111 adds evidence-file output for the final live-client gate run; Batch 112 adds generation time/cwd evidence metadata; Batch 113 adds git source metadata; Batch 114 keeps the source metadata evidence coverage independent of a real git checkout; Batch 115 adds explicit evidence schema versioning; Batch 116 adds checked env names without values; Batch 117 adds evidence-file validation; Batch 118 adds require-run evidence validation; Batch 119 adds execution exit-code consistency validation; Batch 120 adds execution log capture; Batch 121 adds required-log evidence validation; Batch 122 adds relative log path validation; Batch 123 adds portable evidence bundle generation; Batch 124 adds strict execution-command matching; Batch 125 adds log integrity validation; Batch 126 adds selected-gate/readiness strictness; Batch 127 adds execution-set strictness; Batch 128 adds gate-list uniqueness | Live client tests auto-skip when no model is configured; run them as active live tests in an environment with real model config, archive evidence as `<bundle>/evidence.json` plus relative `--evidence-log-dir logs`, and validate it with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`. |
| Memory recall remains behavior-compatible in Phase 1 | **Verified by focused coverage** | DB memory storage tests, memory updater tests, memory prompt injection tests, Batch 82 DB-mode fail-closed guard prevents file-backed Memory fallback, Batch 85 exposes typed save-retry exhaustion diagnostics, no query/embedding recall introduced | Include memory prompt/injection suite in final verification. |
| Skills metadata can be listed without loading full file contents | **Verified for DB public and custom skills** | Batch 62 adds `read_skill_file()` / `list_skill_file_manifest()` and stops DB skill listing from materializing support files. Batch 63 adds metadata caching for custom skills. The close-out batch adds normalized `skills` / `skill_files`, backfills legacy custom rows, imports public and custom skills into DB, fails closed when public DB seed data is missing, and keeps support-file reads on demand. | Keep in final regression suite; public skill release/version rollout remains an operator/product process. |
| Prompt-exposed skill paths exist in sandbox before model reads them | **Verified for LocalSandbox, local Docker AIO, bundled provisioner manifests, and remote smoke entrypoint / Remote live-smoke execution gate** | Sandbox manifest builder/materializer tests; DB skill prompt cache freshness test; LocalSandbox DB skill integration; Batch 65 proves DB-backed `SKILL.md`, text support files, and binary assets are read from storage APIs; Batch 67 maps `skills/...` manifest entries to `skills.container_path` and verifies a live Docker AIO can read `/mnt/skills/custom/live/SKILL.md` and a binary asset after file-API materialization; Batch 70 sends writable mount requirements to the remote provisioner payload; Batch 72 verifies the bundled provisioner maps `extra_mounts` into K8s volumes/mounts and replaces the default read-only `/mnt/skills` mount; Batch 74 adds `tests/test_aio_sandbox_remote_live.py` for real provisioner/K8s execution; Batches 75-77 add `live`, `requires_llm`, `docker_live`, and `remote_live` pytest markers for explicit gate collection; Batch 77 verifies local Docker lifecycle E2E with `3 passed`; Batch 78 adds an automated marker regression guard over live opt-in test sources; Batch 89 makes runtime-context revisions content-sensitive while preserving manifest hash no-op behavior; Batch 91 records provisioner mount-contract hashes and rejects incompatible existing Pods with HTTP 409; Batch 92 keeps provisioner create response bodies visible in `RemoteSandboxBackend` errors; Batch 93 keeps anonymous remote creates compatible with the bundled provisioner request model by falling back to `sandbox_id` for the provisioner `thread_id`; Batch 94 adds a preflight CLI that checks remote live opt-in/provisioner/host-path environment before invoking the remote smoke; Batch 102 makes that preflight reject malformed remote-live env values before pytest invocation; Batch 103 applies the same env-shape validation to the direct remote smoke pytest entrypoint | Run the opt-in remote smoke to prove the deployed cluster accepts the hostPath/PVC mapping and the AIO file API user can write to `skills.container_path`. |
| MCP config changes invalidate tool cache and session pool safely | **Verified by focused and lazy-reload coverage** | MCP cache revision tests, Batch 73 `get_cached_mcp_tools()` lazy-reload smoke, MCP config secret/API tests, direct MCP writes bump extensions revision, session reset paths tied to cache reset, Batch 81 embedded-client DB `update_mcp_config()` resets the MCP tools cache after runtime config writes, Batch 84 ensures DB-mode cache freshness skips local extensions file mtime resolution and relies on DB revision | Keep in final regression suite. |
| Migration dry-run reports stateless incompatibilities before apply | **Verified for V1 resources** | Import inventory/preflight/apply/overwrite tests, CLI DB URL/diagnostic/help tests, Batch 79 `applied.restart_required.app_config` report for overwritten startup-only app config fields, Batch 80 operator runbook checklist/sample for `applied.restart_required`, Batch 86 package-level skill size preflight with operator runbook guidance, Batch 87 stable machine-readable preflight error codes, Batch 88 clean-stderr success contract for real CLI apply subprocesses, close-out public/custom skill `import-to-db` reporting, Batch 98 `extensions.risks` reporting for `env-ref` alongside `resolved-secret` and `stdio-mcp-stateful`, close-out `extensions.mcp_compatibility`, Batch 99 `config.risks` reporting for sensitive app config fields such as `models[].api_key`, Batch 100 `channel_runtime` discovery plus channel runtime credential risks and invalid JSON preflight errors, Batch 101 `applied.channel_runtime_config` and `channel_runtime_config` conflict coverage | Keep stdio MCP compatibility warnings visible in docs and live-gate evidence. |
| Production docs state HTTP/SSE MCP support and stdio MCP compatibility limits | **Verified by documentation and live-gate preflight** | Technical design, runbook, migration reporting, `mcp_stateless` live gate | Keep in runbook; do not claim generic multi-node stdio MCP support. |

## Task-by-Task Audit

### Task 1: Config Source Abstraction

Status: **Verified by focused coverage, with remote provisioner gate**

Evidence:

- `backend/packages/harness/deerflow/config/sources.py`
- `backend/packages/harness/deerflow/config/app_config.py`
- `backend/packages/harness/deerflow/config/bootstrap.py`
- `backend/tests/test_config_sources.py`
- `backend/tests/test_gateway_db_config_startup.py`
- `backend/tests/test_runtime_config_store.py`
- `backend/tests/test_runtime_lifecycle_e2e.py::test_db_mode_stream_run_completes_without_local_runtime_config_files`

Notes:

- DB mode intentionally requires async startup preload; sync `get_app_config()` raises if DB mode is enabled before preload.
- Startup-only field registry exists in `config/reload_boundary.py`; Batch 79 exposes it through repository upsert metadata, migration apply reports, and the read-only admin endpoint `GET /api/config/reload-boundary`.
- Batch 64 proves a DB-mode gateway/run lifecycle can start from `DEER_FLOW_CONFIG_SOURCE=db` and `DEER_FLOW_DATABASE_URL` while `DEER_FLOW_CONFIG_PATH` and `DEER_FLOW_EXTENSIONS_CONFIG_PATH` point to absent files.

Next action:

- Keep K8s/provisioner writable skills-path proof as a production rollout gate.

### Task 2: DB Runtime Config Models

Status: **Verified**

Evidence:

- `backend/packages/harness/deerflow/persistence/runtime_config/model.py`
- `backend/packages/harness/deerflow/persistence/runtime_config/sql.py`
- `backend/packages/harness/deerflow/persistence/models/__init__.py`
- `backend/tests/test_runtime_config_store.py`

Notes:

- Revision increment and stable content hash are covered.
- Bootstrap DB URL remains outside DB, matching the plan.
- Batch 79 adds top-level changed-field metadata for app config updates and maps changed startup-only fields to restart-required reasons.

### Task 3: Agent DB Store

Status: **Verified for custom agents, profiles, default-agent SOUL, API/tool paths**

Evidence:

- `backend/packages/harness/deerflow/config/agent_store.py`
- `backend/packages/harness/deerflow/persistence/agents/model.py`
- `backend/packages/harness/deerflow/config/agents_config.py`
- `backend/app/gateway/routers/agents.py`
- `backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py`
- `backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py`
- `backend/tests/test_db_agent_store.py`
- `backend/tests/test_custom_agent.py`
- `backend/tests/test_setup_agent_tool.py`
- `backend/tests/test_update_agent_tool.py`

Notes:

- Default-agent SOUL is now DB-backed in DB mode and has revision-aware API writes.
- Sync DB store lifecycle remains a V1 tradeoff, not a completion blocker.

### Task 4: Memory DB Storage

Status: **Verified for V1 whole-JSON memory**

Evidence:

- `backend/packages/harness/deerflow/persistence/memory/model.py`
- `backend/packages/harness/deerflow/agents/memory/storage.py`
- `backend/packages/harness/deerflow/agents/memory/updater.py`
- `backend/tests/test_db_memory_storage.py`
- `backend/tests/test_memory_updater.py`
- `backend/tests/test_memory_prompt_injection.py`

Notes:

- Missing rows, user/agent scope, caller-copy behavior, revision freshness, and stale-save rejection are covered.
- Memory updater has bounded retry/merge handling and exposes typed save-retry exhaustion diagnostics through `MemoryUpdater.last_failure_reason`.
- Whole JSON blob storage remains the V1 design.
- Batch 82 ensures DB mode raises when DB memory storage cannot initialize instead of falling back to `FileMemoryStorage`; file mode fallback remains backward-compatible.

### Task 5: Skills DB Storage And Progressive Loading

Status: **Verified for V1 custom skills / Design Divergence**

Evidence:

- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/packages/harness/deerflow/persistence/skills/model.py`
- `backend/packages/harness/deerflow/skills/storage/skill_storage.py`
- `backend/app/gateway/routers/skills.py`
- `backend/packages/harness/deerflow/tools/skill_manage_tool.py`
- `backend/tests/test_db_skill_storage.py`
- `backend/tests/test_skills_custom_router.py`
- `backend/tests/test_skill_manage_tool.py`
- `backend/tests/test_lead_agent_prompt.py`

What is verified:

- Custom `SKILL.md` is persisted in DB.
- Support files are persisted in DB JSON, including binary assets and script mode metadata.
- Explicit support-file delete updates DB source of truth.
- DB-mode skill enabled toggle writes extensions runtime config.
- Prompt cache detects DB revision changes and no longer advertises disabled DB skills.
- Batch 62 adds storage-level `read_skill_file()` and `list_skill_file_manifest()` APIs.
- Batch 62 changes DB listing so it materializes only `SKILL.md`, leaving support files for explicit materialization/read paths.
- Batch 63 adds `custom_skills.metadata_json` and stores parsed metadata when `SKILL.md` is written or installed.
- Batch 63 changes `DbSkillStorage.load_skills()` so custom skill listing uses DB metadata and does not parse materialized `SKILL.md`; stale cache content is overwritten from DB as a side effect of listing.
- Batch 63 adds old-table compatibility for existing `custom_skills` tables missing `metadata_json`, with fallback metadata extraction/backfill.
- The close-out batch adds normalized `skills` and `skill_files` tables, backfills legacy custom rows, dual-writes custom updates, seeds/imports public skills, and makes public/custom DB rows the DB-mode read path.
- The close-out batch keeps progressive loading: list reads metadata/manifest, while support files and binary assets are read on demand.

Gaps:

- Public skill release/version rollout and per-file ACLs are not implemented.
- The legacy `custom_skills` table remains for compatibility/backfill.

Next action:

- Keep normalized public/custom skill storage in focused and full backend regression suites.

### Task 6: MCP DB Store And Cache Revision

Status: **Verified for V1 DB config/runtime paths**

Evidence:

- `backend/packages/harness/deerflow/config/mcp_store.py`
- `backend/packages/harness/deerflow/persistence/mcp/model.py`
- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/packages/harness/deerflow/config/extensions_config.py`
- `backend/app/gateway/routers/mcp.py`
- `backend/packages/harness/deerflow/mcp/cache.py`
- `backend/tests/test_mcp_db_store.py`
- `backend/tests/test_extensions_config_sources.py`
- `backend/tests/test_mcp_config_secrets.py`
- `backend/tests/test_mcp_cache_revision.py`

Notes:

- DB-backed `ExtensionsConfig` view is preserved.
- Dedicated MCP rows are used and legacy aggregate payloads are migrated.
- Secret masking and masked round-trip are covered.
- DB revision changes invalidate MCP tools cache; Batch 73 proves the public `get_cached_mcp_tools()` entrypoint rebuilds tools after a DB revision change.
- Direct MCP-store writes bump the aggregate extensions revision.
- Direct MCP-store writes remain full replacements, which is acceptable if documented.
- Batch 81 closes the embedded Python client write surface: `DeerFlowClient.update_mcp_config()` and `DeerFlowClient.update_skill()` now write DB runtime config in DB mode without resolving `extensions_config.json`.

### Task 7: Sandbox Materializer

Status: **Verified by local/contract/live Docker AIO tests**

Evidence:

- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/packages/harness/deerflow/sandbox/tools.py`
- `backend/packages/harness/deerflow/sandbox/middleware.py`
- `backend/packages/harness/deerflow/config/sandbox_config.py`
- `backend/tests/test_sandbox_materializer.py`
- `backend/tests/test_sandbox_middleware.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `backend/tests/test_aio_sandbox_remote_live.py`
- `backend/tests/test_local_sandbox_provider_mounts.py`

What is verified:

- Manifest first write, manifest hash no-op, pruning, safe relative paths.
- Runtime context builder reads DB memory, agent/profile/SOUL, default-agent SOUL, and skills.
- Lazy/eager sandbox lifecycle paths call materialization in DB mode.
- Strict `runtime_context_fail_closed` can raise instead of failing open.
- LocalSandbox can read DB-materialized custom skills.
- Batch 65 changes sandbox runtime-context skill collection to use `SkillStorage.list_skill_file_manifest()` and `read_skill_file()`, so DB support files and binary assets are included even when the host skill cache only contains `SKILL.md` or is absent before listing.
- Batch 65 adds an `AioSandbox` contract test proving text context files use remote `file.write_file()` and binary context files use base64 `file.write_file(..., encoding="base64")`.
- Batch 67 maps skill manifest entries to the prompt-exposed `skills.container_path` rather than `/tmp/deerflow/context/skills`, adds a DB-mode AIO writable per-thread skills mount for local Docker containers, and verifies a live Docker AIO reads `/mnt/skills/custom/live/SKILL.md` plus a binary asset written through the file API.
- Batch 70 forwards `extra_mounts` to the remote provisioner create payload so the gateway no longer silently drops the writable skills-path contract for provisioner/K8s mode.
- Batch 72 makes the bundled provisioner consume `extra_mounts`, translate them to K8s `hostPath` volumes, and let request-scoped mounts override default mount paths such as `/mnt/skills`.
- Batch 74 adds an opt-in remote provisioner/K8s live smoke that sends the writable skills mount through `RemoteSandboxBackend`, materializes text and binary context files via the sandbox file API, and verifies the prompt-exposed skills path inside the sandbox.
- Batch 75 marks live gates with pytest markers, so `pytest -m live` collects model/client/local-AIO/remote-AIO gates and `pytest -m remote_live` narrows to cluster-backed gates.
- Batch 76 extends the marker coverage to `test_client_e2e.py` real-LLM subtests and `test_deferred_tool_promotion_real_llm.py`, while keeping non-LLM client E2E tests out of the `requires_llm` selection.
- Batch 77 adds `docker_live` for Docker-backed E2E tests and verifies `test_sandbox_orphan_reconciliation_e2e.py` with `3 passed` on this workstation.
- Batch 78 adds `test_live_gate_markers.py`, which scans test sources for real-LLM, remote sandbox, and Docker opt-in signals and fails if the expected pytest markers are missing.
- Batch 89 adds a deterministic file-content snapshot hash to runtime-context revisions, so the revision changes when memory, agent/profile/SOUL, or skill content changes under the same user/agent/skill selection.
- Batch 91 adds bundled provisioner mount-contract annotations and rejects existing Pods whose stored contract does not match the current `extra_mounts` request.
- Batch 92 preserves provisioner create response bodies in `RemoteSandboxBackend` RuntimeErrors so remote smoke failures include provisioner remediation details.
- Batch 93 sends `thread_id or sandbox_id` to the provisioner create API, preventing anonymous remote sandbox creates from submitting `thread_id: null` to the bundled provisioner's string-only request model.
- Batch 94 adds `scripts/check_stateless_live_gates.py` so operators and automation can preflight live-gate environment readiness and get exact pytest commands before invoking remote, Docker, or model-backed gates.
- Batch 95 adds `requires_llm` model-config readiness checks to the preflight CLI, including missing `config.yaml`, empty `models`, invalid `models`, and minimally valid model entries.
- Batch 96 makes those `requires_llm` checks config-source aware, so DB mode reads `runtime_configs.app` through `DEER_FLOW_DATABASE_URL` instead of requiring a local `config.yaml`.
- Batch 97 makes those checks scan file/DB model entries for `$ENV_NAME` values such as `api_key: $OPENAI_API_KEY` and report absent variables through `missing_env`.
- Batch 104 makes the direct `tests/test_client_live.py` entrypoint preload DB-backed AppConfig through the shared bootstrap DB loader before its module-level skip decision, so direct pytest execution no longer falls back to file-only readiness semantics in DB mode.
- Batch 105 makes file-mode `requires_llm` preflight honor `DEER_FLOW_CONFIG_PATH` before falling back to project-root `config.yaml`, matching the runtime AppConfig resolution contract.
- Batch 106 makes DB-mode `requires_llm` preflight derive the actual runtime database URL through `database_config_from_url()`, so sqlite bootstrap URLs follow the same `{sqlite_dir}/deerflow.db` convention as gateway startup.
- Batch 107 makes direct real-LLM E2E entrypoints (`tests/test_client_e2e.py` and `tests/test_create_deerflow_agent_live.py`) use the shared `requires_llm` readiness helper and active AppConfig/model factory, so provider-neutral file/DB model configs do not get skipped merely because `OPENAI_API_KEY` is absent.
- Batch 108 makes the direct live-client entrypoint (`tests/test_client_live.py`) use that same shared `requires_llm` readiness helper before active file/DB AppConfig preload, removing the last duplicated live-client skip decision.
- Batch 109 makes file-mode `requires_llm` preflight honor `DEER_FLOW_PROJECT_ROOT/config.yaml` when `DEER_FLOW_CONFIG_PATH` is absent, matching the runtime project-root resolution contract and reporting invalid project-root paths as `config_issues`.
- Batch 111 adds `--evidence-path` to the live-gate preflight CLI so remote/model gate runs can emit a durable JSON record containing the preflight state and any executed pytest commands with exit codes.
- Batch 112 adds `generated_at_utc` and `cwd` to live-gate evidence JSON so archived external-gate records carry basic provenance metadata.
- Batch 113 adds git source metadata (`repo_root`, `head`, `branch`, `dirty`, `status_short_count`) to live-gate evidence JSON when git is available.
- Batch 114 makes the source-metadata evidence test inject deterministic git metadata, keeping the coverage valid even when tests run from a source tree without `.git`.
- Batch 115 adds `schema_version: 1` to live-gate evidence JSON so archived rollout records have an explicit parser compatibility marker.
- Batch 116 adds `checked_env` to each live-gate report, listing checked environment variable names without storing their values.
- Batch 117 adds `--validate-evidence` so archived live-gate evidence can be checked for supported schema and successful recorded outcome.
- Batch 118 adds `--require-run` to evidence validation so preflight-only evidence cannot satisfy rollout sign-off.
- Batch 119 rejects contradictory successful evidence when a recorded execution has a non-zero `exit_code`.
- Batch 120 adds `--evidence-log-dir` so executed gate stdout/stderr can be archived beside JSON evidence.
- Batch 121 adds `--require-logs` so archived evidence validation can require existing stdout/stderr log files for every execution.
- Batch 122 resolves relative execution log paths from the evidence file directory during `--require-logs` validation.
- Batch 123 makes relative `--evidence-log-dir` generation write beside the evidence file and record relative `logs/...` paths, so produced evidence bundles are portable by default.
- Batch 124 makes `--require-run` validate that each selected gate execution command exactly matches the command recorded in preflight evidence.
- Batch 125 records SHA-256 and byte counts for captured stdout/stderr logs and makes `--require-logs` reject missing or mismatched log integrity metadata.
- Batch 126 makes `--require-run` reject empty selected-gate lists, unknown selected gates, not-ready top-level preflight evidence, and selected preflight gates whose `ready_to_invoke` state is not true.
- Batch 127 makes `--require-run` reject unselected execution gates and duplicate execution records.
- Batch 128 makes `--require-run` reject duplicate selected gates and duplicate selected preflight gate entries.

Gaps:

- The remote K8s/provisioner live smoke entrypoint exists, but it still needs to be executed in the deployed cluster because unit tests cannot prove hostPath/PVC permissions there.
- Host-path translation for Docker/DooD deployments remains documented as a production contract risk.

Next action:

- Run the opt-in remote provisioner/K8s smoke as an explicit operator rollout gate.

### Task 8: Migration And Dry-run

Status: **Verified for V1 resources**

Evidence:

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`

What is verified:

- Dry-run inventory and non-mutating preflight.
- App config, extensions/MCP, agents, user profiles, default-agent SOUL, memory, and public/custom skills apply paths.
- Conflict detection and explicit overwrite.
- Semantic preflight for app config, extensions, agents, memory shape, oversized skill files, and oversized skill packages.
- App config, channel runtime, and MCP migration risks for `env-ref`, `resolved-secret`, and `stdio-mcp-stateful`, plus `extensions.mcp_compatibility`.
- Stable `preflight.errors[].code` values for scripted migration remediation.
- Apply reports changed startup-only app config fields under `applied.restart_required.app_config`.
- CLI `DEER_FLOW_DATABASE_URL`, `--database-url`, diagnostics, help import hygiene, and clean stderr on successful apply subprocesses.

Gaps:

- Public skill release/version rollout remains an operator/product process; runtime DB seed/import is implemented.

## Stale Review Items That Should Not Drive New Work Directly

These older findings are superseded by later batches, but the old files still contain open-looking headings:

- DB memory stale cache and stale overwrite concerns are superseded by DB memory revision freshness and memory updater retry batches.
- App config startup wiring and extensions DB startup concerns are superseded by DB startup preload and DB-backed extensions batches.
- MCP legacy aggregate migration and direct write revision concerns are superseded by MCP legacy migration and direct-store revision bump batches.
- Default-agent global SOUL file-backed concerns are superseded by default-agent SOUL runtime, migration, API, and revision batches.
- Skill prompt cache true DB toggle gap is superseded by Batch 60.
- CLI subprocess warning-noise concerns are superseded by Batch 88 clean-stderr apply coverage.

Do not treat those headings as current blockers without checking the later review documents and implementation log.

## Current Highest-Priority Remaining Work

1. **Execute remote provisioner/K8s live smoke**
   - Batch 67 verifies local Docker AIO with a writable per-thread `skills.container_path` mount.
   - Batch 70 verifies the gateway forwards writable mount requirements to `RemoteSandboxBackend` / provisioner as `extra_mounts`.
   - Batch 72 verifies the bundled provisioner consumes `extra_mounts` and renders K8s volumes/mounts that override `/mnt/skills`.
   - Batch 74 adds the opt-in smoke command and test entrypoint.
   - Batch 75 adds pytest markers so operators can collect/run all live gates with `-m live` or remote-only gates with `-m remote_live`.
   - Batch 76 extends marker coverage to additional real-LLM E2E gates and verifies `-m live` collects 36 gates, `-m requires_llm` collects 34 model-backed gates, and `-m remote_live` collects the 1 cluster-backed gate.
   - Batch 77 adds `docker_live` and verifies `-m docker_live` collects/runs the 3 local Docker lifecycle E2E tests.
   - Batch 78 adds an automated marker regression guard so new opt-in live tests cannot silently bypass `-m live` / specialized marker selection.
   - Batch 79 exposes startup-only restart metadata in DB runtime config updates, migration reports, and admin-readable config metadata.
   - Batch 80 documents the migration report field and admin reload-boundary endpoint in the operator runbook.
   - Batch 81 closes embedded-client DB-mode MCP/skill update writes, so local Python callers no longer fall back to `extensions_config.json`.
   - Batch 82 closes a Memory persistence escape hatch by making DB mode fail closed instead of falling back to `FileMemoryStorage`.
   - Batch 83 aligns MCP/skill update OpenAPI contracts with the active extensions configuration source instead of file-only wording.
   - Batch 84 removes a hidden DB-mode MCP cache dependency on local extensions config file mtime/path resolution.
   - Batch 85 adds typed Memory updater save-retry exhaustion diagnostics while preserving the existing bool return API.
   - Batch 86 blocks oversized DB-backed custom skill packages during migration preflight and documents the package-size threshold.
   - Batch 87 adds stable machine-readable migration preflight error codes while preserving operator-readable messages.
   - Batch 88 closes the remaining local CLI warning-noise gap for successful runtime-state import apply subprocesses.
   - Batch 89 makes sandbox runtime-context revisions content-sensitive while keeping manifest hash no-op semantics intact.
   - Batch 90 makes public-skill deployment artifact requirements visible in runtime-state import skill rows.
   - Batch 91 prevents bundled provisioner idempotent creates from reusing an existing Pod with a stale or incompatible mount contract.
   - Batch 92 preserves provisioner response-body details when remote create fails, so operator-visible remote smoke output keeps the remediation text.
   - Batch 93 keeps anonymous remote sandbox create requests provisioner-compatible by sending `sandbox_id` as the fallback provisioner `thread_id`.
   - Batch 94 adds `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --json` and `--run` as a machine-checkable preflight/execution wrapper.
   - Batch 102 rejects malformed `remote_live` env values such as relative host paths, non-HTTP provisioner URLs, relative container paths, and invalid ready timeouts before invoking pytest.
   - Batch 103 applies that env-shape validation inside `tests/test_aio_sandbox_remote_live.py` too, so direct pytest execution fails early with actionable messages before creating a remote sandbox.
   - Batch 111 lets operators add `--evidence-path <file>` to record the preflight and executed command exit codes as JSON during remote smoke sign-off.
   - Batch 112 adds generation timestamp and cwd metadata to that evidence file.
   - Batch 113 adds git source metadata to that evidence file.
   - Batch 114 hardens the source-metadata evidence test so it no longer depends on the local checkout's `.git` directory.
   - Batch 115 adds an explicit `schema_version` to the evidence file for parser compatibility during rollout record archival.
   - Batch 116 adds `checked_env` names to the gate report without storing environment variable values.
   - Batch 117 adds `--validate-evidence` so archived remote smoke evidence can be checked after the gate runs.
   - Batch 118 adds `--validate-evidence --require-run` for remote smoke sign-off, ensuring the archived evidence includes execution records.
   - Batch 119 ensures that remote smoke sign-off cannot claim `overall_exit_code: 0` while the recorded remote-live execution has a non-zero `exit_code`.
   - Batch 120 adds `--evidence-log-dir` for remote smoke sign-off, so the JSON evidence can point to archived stdout/stderr logs.
   - Batch 121 adds `--require-logs` for remote smoke sign-off validation, so missing log artifacts fail archived evidence validation.
   - Batch 122 makes relative remote-smoke log paths validate from the evidence directory, supporting portable archived bundles.
   - Batch 123 makes `--evidence-path <bundle>/evidence.json --evidence-log-dir logs` write remote-smoke logs under `<bundle>/logs/` while preserving relative paths in the evidence JSON.
   - Batch 124 makes remote-smoke strict validation reject evidence whose `remote_live` execution command differs from the preflight command.
   - Batch 125 makes remote-smoke strict log validation reject tampered or truncated stdout/stderr log artifacts.
   - Batch 126 makes remote-smoke strict validation reject evidence that does not select a known gate with ready top-level and per-gate preflight state.
   - Batch 127 makes remote-smoke strict validation reject evidence whose execution set is not one-to-one with selected gates.
   - Batch 128 makes remote-smoke strict validation reject evidence with duplicate selected or selected-preflight gate entries.
   - Production K8s/provisioner sandboxes still need that smoke to be executed against the deployed cluster/node path or PVC permissions to prove they are writable by the AIO shell/file API user.

2. **Public/custom skill DB storage is now implemented**
   - Normalized `skills` and `skill_files` rows are the DB-mode source of truth for public and custom skills.
   - Public skills are seeded/imported into DB and missing seed data fails closed.
   - Custom skill writes still maintain `custom_skills` for compatibility while dual-writing normalized rows.
   - Progressive loading remains: list uses metadata/manifest, support files are read on demand.

3. **Live client model-config gate**
   - Full backend verification now passes in the no-model environment: `4936 passed, 36 skipped, 12 warnings`.
   - `tests/test_client_live.py` is skipped automatically when no models are configured.
   - Batch 104 verifies that, when `DEER_FLOW_CONFIG_SOURCE=db` and `runtime_configs.app` contains a model, direct import/collection of `tests/test_client_live.py` preloads DB AppConfig through the shared bootstrap loader, does not append a false skip marker, and does not log the pre-preload `get_app_config()` error.
   - Run `tests/test_client_live.py` as active live tests in an environment with real model config, or run all marked live gates with `uv --directory backend run pytest -m live -q`.
   - For model-only gates, run `uv --directory backend run pytest -m requires_llm -q`.
   - Batch 94 also supports `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run`.
   - Batch 95 makes that wrapper fail preflight with `config_issues` when `config.yaml` has no configured models, while still leaving credential validity and model behavior to the live gate itself.
   - Batch 96 makes that wrapper use DB-backed `runtime_configs.app` when `DEER_FLOW_CONFIG_SOURCE=db`, preserving stateless mode semantics.
   - Batch 97 makes that wrapper fail preflight with `missing_env` when a configured model references a missing `$ENV_NAME`, while still leaving credential validity and model behavior to the live gate itself.
   - Batch 104 aligns the direct `tests/test_client_live.py` entrypoint with the shared bootstrap DB preload path; real provider completion still requires the live gate to run with valid credentials.
   - Batch 105 aligns file-mode wrapper behavior with `DEER_FLOW_CONFIG_PATH`, so operators who keep live model config outside the repository root are not falsely blocked by the default `config.yaml`.
   - Batch 106 aligns DB-mode wrapper behavior with bootstrap DB URL parsing, so sqlite URL filenames do not make preflight inspect a different DB file than runtime startup.
   - Batch 107 aligns `test_client_e2e.py` and `test_create_deerflow_agent_live.py` with the same `requires_llm` readiness helper and active AppConfig/model factory, while preserving the legacy synthetic config fallback for non-LLM `test_client_e2e.py` checks in unconfigured workstations.
   - Batch 108 aligns `test_client_live.py` with the same `requires_llm` readiness helper before active file/DB AppConfig preload, so preflight and direct client-live pytest entrypoints use one readiness contract.
   - Batch 109 aligns file-mode wrapper behavior with `DEER_FLOW_PROJECT_ROOT/config.yaml` when no explicit config path is set, so operators who launch from another working directory are not falsely blocked by the script's default project root.
   - Batch 111 lets operators add `--evidence-path <file>` to record the preflight and executed command exit codes as JSON during real model-backed live sign-off.
   - Batch 112 adds generation timestamp and cwd metadata to that evidence file.
   - Batch 113 adds git source metadata to that evidence file.
   - Batch 114 hardens the source-metadata evidence test so it no longer depends on the local checkout's `.git` directory.
   - Batch 115 adds an explicit `schema_version` to the evidence file for parser compatibility during rollout record archival.
   - Batch 116 adds `checked_env` names to the gate report without storing environment variable values.
   - Batch 117 adds `--validate-evidence` so archived real-model evidence can be checked after the gate runs.
   - Batch 118 adds `--validate-evidence --require-run` for model-gate sign-off, ensuring the archived evidence includes execution records.
   - Batch 119 ensures that model-gate sign-off cannot claim `overall_exit_code: 0` while a recorded execution has a non-zero `exit_code`.
   - Batch 120 adds `--evidence-log-dir` for model-gate sign-off, so the JSON evidence can point to archived stdout/stderr logs.
   - Batch 121 adds `--require-logs` for model-gate sign-off validation, so missing log artifacts fail archived evidence validation.
   - Batch 122 makes relative model-gate log paths validate from the evidence directory, supporting portable archived bundles.
   - Batch 123 makes `--evidence-path <bundle>/evidence.json --evidence-log-dir logs` write model-gate logs under `<bundle>/logs/` while preserving relative paths in the evidence JSON.
   - Batch 124 makes model-gate strict validation reject evidence whose `requires_llm` execution command differs from the preflight command.
   - Batch 125 makes model-gate strict log validation reject tampered or truncated stdout/stderr log artifacts.
   - Batch 126 makes model-gate strict validation reject evidence that does not select a known gate with ready top-level and per-gate preflight state.
   - Batch 127 makes model-gate strict validation reject evidence whose execution set is not one-to-one with selected gates.
   - Batch 128 makes model-gate strict validation reject evidence with duplicate selected or selected-preflight gate entries.

## Completion Decision

Do not mark the active goal complete yet.

The implementation has strong subsystem coverage. Batches 63-128 closed the V1 custom-skill metadata-first listing, DB-mode gateway run smoke, DB skill support-file sandbox materialization, local Docker AIO file-API proof for prompt-exposed skill paths, gateway-side provisioner mount-contract forwarding, bundled provisioner `extra_mounts` consumption, bundled provisioner existing-Pod mount-contract validation, remote provisioner create error-detail propagation, remote provisioner anonymous payload compatibility, stateless live-gate preflight automation, remote-live env shape validation in both preflight and direct pytest entrypoints, requires-LLM model-config preflight protection, requires-LLM DB config-source preflight awareness, requires-LLM DB bootstrap URL parsing parity, requires-LLM file-mode `DEER_FLOW_CONFIG_PATH` and `DEER_FLOW_PROJECT_ROOT` awareness, requires-LLM model env-reference preflight protection, direct live-client DB AppConfig preload, provider-neutral direct real-LLM E2E readiness, shared direct live-client readiness, live-gate evidence-file output/strict validation and versioned provenance/source metadata with portable source-metadata, checked-env coverage, require-run validation, selected-gate/top-level/per-gate readiness strictness, execution-set strictness, gate-list uniqueness, execution command matching, execution exit-code consistency checks, execution log capture, required-log validation, log integrity validation, relative-log bundle validation, and portable evidence bundle generation, MCP lazy reload smoke coverage, MCP cache DB-revision invalidation without local extensions-file mtime dependency, the remote provisioner/K8s smoke entrypoint, pytest-marked live gate collection including Docker-backed local lifecycle E2E, automated live-gate marker regression protection, content-sensitive sandbox runtime-context revisions, startup-only restart metadata for DB config writes/migration/admin inspection plus operator runbook guidance, embedded-client DB-mode MCP/skill write parity, DB-mode Memory fail-closed fallback protection, typed Memory updater save-retry exhaustion diagnostics, source-neutral OpenAPI contracts for MCP/skill runtime mutation endpoints, package-level skill size preflight, stable migration preflight error codes, clean-stderr runtime-state import CLI success paths, runtime-state import MCP/app-config/channel-runtime secret and env-reference risk reporting, DB-backed runtime channel credential writes, channel runtime migration apply/conflict coverage, normalized DB-backed public/custom skill storage with public seed/import and fail-closed public reads, memory-updater bounded context snapshots, and strict MCP stateless compatibility preflight/reporting. The remaining completion risks are executing the remote provisioner/K8s live smoke and running live-client verification with a configured model.
