# Harness Stateless DB-backed Runtime State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Harness 从本地运行态文件依赖改造为 DB-backed stateless runtime，并在运行时把必要的
config、agent、memory、skills 信息安全地下发到 AIOSandbox。

**Architecture:** 保留现有 Harness/App 边界，在 Harness 内新增 source/store 抽象和 DB 实现，
先保持现有行为兼容，再逐步移除运行态本地文件依赖。DB 负责持久状态，进程内只保留 revision-aware
cache，AIOSandbox 只接收按 run/thread snapshot 物化出来的只读上下文文件。

**Tech Stack:** Python, FastAPI, Pydantic, SQLAlchemy async ORM, SQLite/Postgres, AIOSandbox,
LangGraph/LangChain MCP tooling.

---

## 1. Scope And Non-goals

### In Scope

- 将 `config.yaml` 中的 Harness runtime config 迁移为 DB-backed config source。
- 将 `extensions_config.json` / legacy `mcp_config.json` 中的 MCP 和 skill enabled state 迁移到 DB。
- 将 custom agents、`SOUL.md`、用户 profile、memory、skills 元数据和文件内容迁移到 DB。
- 在 Harness 运行时向 AIOSandbox 物化 memory、agent、skills 所需文件。
- 保留现有 API 语义，第一版尽量不改变 prompt、memory recall、MCP tool_search 行为。
- 提供 migration/import dry-run 和实际导入命令。

### Non-goals For V1

- 不做零配置启动。DB 连接信息、`DEER_FLOW_CONFIG_SOURCE`、最小 secret provider 配置必须来自 env 或 bootstrap。
- 不在第一版引入 query-based memory recall、embedding recall 或 facts 拆表检索。
- 不在第一版声明 stdio MCP 支持多节点 stateless。stdio MCP 仍属于单节点或 sticky-session 兼容模式。
- 不把 resolved secret 明文写入普通 DB config payload。

---

## 2. Current State Summary

- App config 当前由 `AppConfig.from_file()` 从 `config.yaml` 加载，并合并 `ExtensionsConfig.from_file()`。
- `ExtensionsConfig` 当前统一承载 MCP servers 和 skill enabled state，底层来自 `extensions_config.json` 或
  legacy `mcp_config.json`。
- custom agents 当前是 per-user 文件布局：`users/{user_id}/agents/{agent}/config.yaml` 和 `SOUL.md`。
- memory 当前已有 `MemoryStorage` 抽象，默认 `FileMemoryStorage`，层级为 user global memory 和
  user + agent memory。
- memory recall 当前不是检索式 RAG，而是按 `(user_id, agent_name)` 读取整份 memory JSON，经过
  `max_injection_tokens` 格式化后注入首个 user message；同一 thread 内 memory snapshot 保持冻结。
- skills 当前从本地 `skills/public`、`skills/custom` 扫描，prompt 暴露 `/mnt/skills/.../SKILL.md` 路径。
- AIO local sandbox 当前可通过 bind mount 暴露 skills；remote AIO 不能依赖本地 bind mount，stateless 模式必须通过
  sandbox API 或打包脚本物化文件。
- MCP 当前多处故意使用 `ExtensionsConfig.from_file()` 直接读文件，并用文件 mtime 判断 cache 是否 stale。
- stdio MCP 当前在 Harness 进程内维护 `(server_name, thread_id)` session pool，这与多节点 stateless 有天然张力。

---

## 3. Target Architecture

### 3.1 Source And Store Layer

新增或收敛以下抽象：

- `ConfigSource`
  - `FileConfigSource`: 保持现有文件加载能力。
  - `DbConfigSource`: 从 DB 读取 raw app config，并返回 Pydantic-validated `AppConfig`。
- `ExtensionsConfigSource`
  - 对外继续暴露 `ExtensionsConfig` view。
  - 内部允许 MCP servers、skill states 分表存储。
- `AgentStore`
  - 负责 custom agent config、soul、user profile 的 CRUD。
- `MemoryStorage`
  - 保留 `FileMemoryStorage`。
  - 新增 `DbMemoryStorage`。
- `SkillStorage`
  - 保留 local storage。
  - 新增 DB-backed storage，支持 metadata-first 和 content-on-demand。
- `SandboxMaterializer`
  - 根据 run/thread snapshot 和 DB revision，将需要的文件物化到 sandbox。

所有 DB-backed source/store 必须提供 revision 或 content hash，以便 cache 失效和 sandbox manifest diff。

### 3.2 Bootstrap Boundary

以下配置不进入 DB runtime config，避免启动循环：

- `DEER_FLOW_CONFIG_SOURCE=file|db`
- DB 连接信息，例如 `DEER_FLOW_DATABASE_URL` 或现有 database bootstrap 配置。
- 最小 secret provider 配置。
- 进程级安全开关，例如 MCP stdio command allowlist env。

DB runtime config 里可以保存业务运行配置，但读取 DB 本身所需的信息必须来自 env/bootstrap。

### 3.3 Revision And Cache Invalidation

第一版使用保守的 revision 失效协议：

- 每个 DB config/store payload 都有 `revision` 和 `updated_at`。
- 写入成功必须递增 revision。
- 本进程写入后立即 reset 对应 cache。
- 工具加载、agent 构建、sandbox 物化前轻量检查 revision。
- 后续阶段可升级为 Postgres NOTIFY/LISTEN 或 Redis/pubsub。

---

## 4. Data Model Plan

### 4.1 Runtime Config

`runtime_configs`

- `key`: `app`, `extensions`, `channel_runtime` 等逻辑配置名。
- `payload_json`: raw config payload，保持原始 env ref，不存 resolved secret。
- `schema_version`
- `revision`
- `config_hash`
- `created_at`
- `updated_at`
- `updated_by`

启动边界：

- `database`、`checkpointer`、`run_events`、`stream_bridge`、`sandbox`、`channels` 等 startup-only 字段变更后，
  API 应返回需要 restart/rebuild runtime 的提示。
- 非 startup-only 字段可按 revision 热更新。

### 4.2 MCP

建议分表，而不是只塞在 `runtime_configs.extensions` 里：

`mcp_servers`

- `name`
- `enabled`
- `transport_type`: `stdio`, `http`, `sse`
- `command`
- `args_json`
- `url`
- `env_refs_json`
- `headers_json`
- `oauth_json`
- `description`
- `extra_json`
- `revision`
- `config_hash`
- `created_at`
- `updated_at`

约束：

- GET API 必须 mask env/header/oauth secrets。
- PUT API 必须 preserve masked secrets，不能把 `***` 写成真实配置。
- 第一版只支持 env ref + masked round-trip，不保存 resolved secret 明文。
- `mcpInterceptors` 不开放普通 API 任意写入，只允许 migration 导入或 allowlist 管理。

### 4.3 Agents

`custom_agents`

- `owner_user_id`
- `agent_name`
- `config_json`
- `soul_text`
- `revision`
- `created_at`
- `updated_at`
- unique(`owner_user_id`, `agent_name`)

`user_profiles`

- `owner_user_id`
- `profile_text`
- `revision`
- `created_at`
- `updated_at`

语义保持：

- `assistant_id -> agent_name` 不变。
- agent name validation 不变。
- model validation 不变。
- legacy shared agents 只读 fallback 可保留到 migration 完成后再移除。

### 4.4 Memory

`memories`

- `owner_user_id`
- `agent_scope`: 空字符串代表用户全局 memory，agent 名代表 user + agent memory。
- `memory_json`
- `schema_version`
- `revision`
- `last_updated`
- `created_at`
- `updated_at`
- unique(`owner_user_id`, `agent_scope`)

第一版保留完整 JSON blob，确保 `format_memory_for_injection()` 和 updater 行为不变。

并发写策略：

- 保存时带 revision 条件更新。
- revision 冲突时重读最新 memory，并用已解析的 update 重放一次。
- 第二次仍冲突则记录失败并跳过，不允许盲写覆盖。

### 4.5 Skills

`skills`

- `category`: `public`, `custom`
- `name`
- `description`
- `license`
- `enabled`
- `metadata_json`
- `revision`
- `content_hash`
- `created_at`
- `updated_at`
- unique(`category`, `name`)

`skill_files`

- `skill_category`
- `skill_name`
- `relative_path`
- `content_bytes` 或 `content_text`
- `mime_type`
- `content_hash`
- `revision`
- unique(`skill_category`, `skill_name`, `relative_path`)

`skill_history`

- `skill_category`
- `skill_name`
- `action`
- `payload_json`
- `created_at`

渐进式加载策略：

- prompt/list 阶段只加载 metadata 和 enabled state。
- 暴露 `/mnt/skills/.../SKILL.md` 前，必须至少物化 `SKILL.md`。
- slash activation 或首次使用某 skill 后，再按需物化完整文件树和 assets。

---

## 5. Subsystem Implementation Plan

Tracking note, 2026-06-20: the checkboxes below reflect the current V1 implementation status from
`docs/harness-stateless-db-mode-requirement-audit.md` and the per-batch implementation log. Public
skill DB seeding and normalized skill tables are recorded as V1 scope decisions, and remote
provisioner/K8s execution remains a rollout proof gate.

### Task 1: Config Source Abstraction

**Files:**

- Create: `backend/packages/harness/deerflow/config/sources.py`
- Modify: `backend/packages/harness/deerflow/config/app_config.py`
- Modify: `backend/packages/harness/deerflow/config/extensions_config.py`
- Test: `backend/tests/test_config_db_source.py`

Steps:

- [x] Add failing tests for `FileConfigSource` parity with current file loading.
- [x] Add failing tests for `DbConfigSource` loading raw payload by key and revision.
- [x] Implement source interfaces without changing default file behavior.
- [x] Change `get_app_config()` cache signature from file-only to source revision aware.
- [x] Preserve reload boundary behavior.
- [x] Run focused config tests.

### Task 2: DB Runtime Config Models

**Files:**

- Create: `backend/packages/harness/deerflow/persistence/runtime_config/model.py`
- Modify: `backend/packages/harness/deerflow/persistence/models/__init__.py`
- Test: `backend/tests/test_runtime_config_store.py`

Steps:

- [x] Add failing ORM tests for insert, update, revision increment, hash change.
- [x] Add SQLAlchemy model and repository/store helpers.
- [x] Ensure `Base.metadata.create_all` discovers the table.
- [x] Run focused persistence tests.

### Task 3: Agent DB Store

**Files:**

- Create: `backend/packages/harness/deerflow/config/agent_store.py`
- Create: `backend/packages/harness/deerflow/persistence/agents/model.py`
- Modify: `backend/packages/harness/deerflow/config/agents_config.py`
- Modify: `backend/app/gateway/routers/agents.py`
- Modify: agent setup/update tools.
- Test: existing agent router/tool tests plus new DB store tests.

Steps:

- [x] Add failing tests for create/update/list/delete custom agents in DB.
- [x] Add failing tests for user isolation.
- [x] Add failing tests for legacy file fallback read behavior.
- [x] Implement `DbAgentStore`.
- [x] Route agent API and setup/update tools through `AgentStore`.
- [x] Preserve `assistant_id -> agent_name` semantics.

### Task 4: Memory DB Storage

**Files:**

- Create: `backend/packages/harness/deerflow/persistence/memory/model.py`
- Modify: `backend/packages/harness/deerflow/persistence/models/__init__.py`
- Modify: `backend/packages/harness/deerflow/agents/memory/storage.py`
- Test: `backend/tests/test_db_memory_storage.py`
- Test: existing memory storage/updater/router tests.

Steps:

- [x] Add failing tests for missing memory returning `create_empty_memory()`.
- [x] Add failing tests for user global and user + agent scoping.
- [x] Add failing tests for caller dict not being mutated on save.
- [x] Add failing tests for revision conflict retry behavior.
- [x] Implement `DbMemoryStorage`.
- [x] Keep recall behavior unchanged.
- [x] Run memory test suite.

### Task 5: Skills DB Storage And Progressive Loading

**Files:**

- Create: `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- Create: `backend/packages/harness/deerflow/persistence/skills/model.py`
- Modify: `backend/packages/harness/deerflow/skills/storage/skill_storage.py`
- Modify: skill router and skill management tool paths.
- Modify: skill activation middleware where it directly assumes local file paths.
- Test: skills storage/router/activation tests.

Steps:

- [x] Add failing tests for metadata-only listing.
- [x] Add failing tests for reading `SKILL.md` through storage API.
- [x] Add failing tests for installing/updating skill files into DB.
- [x] Add failing tests for enabled state compatibility with `ExtensionsConfig` view.
- [x] Implement `DbSkillStorage`.
- [x] Update prompt/list code to avoid eager loading full file trees.
- [x] Keep local storage as fallback.

Close-out update: DB-backed skill storage now uses normalized `skills` / `skill_files` rows for both
public and custom skills. Public skills are platform-owned/read-only content, but strict DB mode
requires them to be seeded/imported into DB and fails closed when the DB seed is missing.

### Task 6: MCP DB Store And Cache Revision

**Files:**

- Create: `backend/packages/harness/deerflow/persistence/mcp/model.py`
- Create: `backend/packages/harness/deerflow/config/mcp_store.py`
- Modify: `backend/packages/harness/deerflow/config/extensions_config.py`
- Modify: `backend/app/gateway/routers/mcp.py`
- Modify: `backend/packages/harness/deerflow/mcp/cache.py`
- Modify: `backend/packages/harness/deerflow/mcp/tools.py`
- Modify: `backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py`
- Modify: `backend/packages/harness/deerflow/sandbox/tools.py`
- Test: MCP config, OAuth, session pool, ACP bridge, filesystem allowed path tests.

Steps:

- [x] Add failing tests for DB-backed `ExtensionsConfig` view.
- [x] Add failing tests for secret masking and masked round-trip against DB.
- [x] Add failing tests for cache reset on revision change.
- [x] Add failing tests for session pool isolation by config hash or reset.
- [x] Replace file mtime stale checks with revision checks.
- [x] Remove direct runtime dependence on `ExtensionsConfig.from_file()` in MCP paths.
- [x] Keep file source for migration/rollback.

### Task 7: Sandbox Materializer

**Files:**

- Create: `backend/packages/harness/deerflow/sandbox/materializer.py`
- Modify: AIO sandbox provider lifecycle.
- Modify: local sandbox provider lifecycle if needed.
- Test: AIO sandbox provider tests and new materializer tests.

Steps:

- [x] Add failing tests for manifest first write.
- [x] Add failing tests for idempotent no-op when revision/hash unchanged.
- [x] Add failing tests for pruning removed skill files.
- [x] Add failing tests for remote AIO path using sandbox API, not bind mount.
- [x] Implement materializer for `skills.container_path` (default `/mnt/skills`) and `/tmp/deerflow/context`.
- [x] Pin run/thread snapshot revisions before materialization.

Rollout gate: local and contract coverage exists, including local Docker AIO. A real remote
provisioner/K8s smoke still must be executed in the target deployment to prove writable
`skills.container_path` permissions.

### Task 8: Migration And Dry-run

**Files:**

- Create: `backend/scripts/import_runtime_state_to_db.py`
- Test: migration dry-run tests.
- Docs: update this document with command examples after implementation.

Steps:

- [x] Add dry-run tests for config, MCP, agents, memory, skills discovery.
- [x] Report env refs, resolved-secret risks, stdio MCP risks, invalid schemas.
- [x] Implement import command with `--dry-run` default.
- [x] Implement explicit `--apply`.
- [x] Preserve file fallback for rollback.

Close-out update: migration imports public and custom skills into DB and reports both with
`import_action: import-to-db`, `storage_boundary: db`, and `runtime_artifact_required: false`.

---

## 6. Rollout Plan

### Phase 1: DB-backed Storage, Behavior Compatible

- Implement source/store abstractions.
- Add DB-backed config, extensions, agents, memory, skills, MCP stores.
- Keep existing prompt, memory recall, MCP tool_search, API behavior unchanged.
- File mode remains default until DB mode passes focused tests.

### Phase 2: Stateless Runtime And Sandbox Materialization

- Enable DB mode via bootstrap.
- Materialize skills, agent context, memory context into AIOSandbox.
- Remove runtime dependency on local writable config/agent/memory/skills files.
- Validate local and remote AIO paths.

### Phase 3: Enhancements

- Split memory facts into queryable rows.
- Add query-based or embedding-based memory recall.
- Move stdio MCP into sidecar or sandbox to support multi-node stateless.
- Add external secret store.
- Add DB notify/pubsub for low-latency cache invalidation.

---

## 7. Test Plan

Run focused suites by subsystem:

- Config/extensions:
  - `uv --directory backend run pytest tests/test_gateway_config_freshness.py tests/test_reload_boundary.py -q`
- Memory:
  - `uv --directory backend run pytest tests/test_memory_storage.py tests/test_memory_storage_user_isolation.py tests/test_memory_updater.py tests/test_memory_updater_user_isolation.py tests/test_memory_router.py tests/test_memory_prompt_injection.py -q`
- Skills:
  - `uv --directory backend run pytest tests/test_skills_custom_router.py tests/test_skills_storage.py -q`
- MCP:
  - `uv --directory backend run pytest tests/test_mcp_config_secrets.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py -q`
- Sandbox:
  - `uv --directory backend run pytest tests/test_aio_sandbox_provider.py tests/test_remote_sandbox_backend.py -q`

Final verification:

- `uv --directory backend run pytest -q`
- `uv --directory backend run ruff check`
- `git diff --check`

If sandbox restrictions block `uv` cache access, rerun the same command with approved elevated permissions rather than changing test commands.

---

## 8. Risks And Decisions

- **Bootstrap loop:** DB connection config cannot live only in DB. Keep bootstrap env explicit.
- **MCP statefulness:** DB stores MCP config, not MCP runtime state. stdio MCP remains incompatible with generic multi-node stateless unless sidecar/sandboxed.
- **Secret leakage:** Store env refs and masked values, not resolved secrets.
- **Memory conflicts:** Whole-JSON memory writes need revision checks and bounded retry.
- **Skill path expectations:** The model reads real `/mnt/skills` files; DB content must be materialized before paths are advertised.
- **Cross-worker drift:** Revision checks are mandatory in every runtime path that currently depends on file mtime or direct file reads.

---

## 9. Acceptance Criteria

- DB mode can start with only bootstrap DB information and no writable local config/agent/memory/skills files.
- Existing file mode remains compatible.
- Config, agent, memory, skills, MCP APIs preserve existing external behavior.
- Memory recall output remains behavior-compatible in Phase 1.
- Enabled skills can be listed without loading all file contents.
- AIOSandbox receives `SKILL.md`, agent context, and memory context files under fixed paths.
- MCP config changes invalidate tool cache and session pool safely.
- Migration dry-run reports all imported resources and stateless incompatibilities before applying changes.
