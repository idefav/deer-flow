# Harness Stateless DB Mode Implementation Log

本文记录 Harness stateless DB mode 的逐批实现步骤。每一批都记录目标、改动、验证和后续风险，避免长期改造过程中丢失上下文。

---

## Batch 1: Runtime Config Store Foundation

Date: 2026-06-19

### Goal

建立 DB-backed runtime config 的第一块基础能力，但不改变现有 file mode 默认行为：

- 增加 bootstrap env helper。
- 增加 `runtime_configs` ORM model。
- 增加 runtime config repository。
- 增加 deterministic JSON hash。
- 将新 ORM model 注册进 persistence model registry。

### Steps

1. 复查方案文档和 refinement 文档，确认第一批应从 bootstrap/runtime config foundation 开始。
2. 读取现有 persistence model/repository/test 风格，确认 repo 使用实体子目录和 `persistence/models/__init__.py` 注册。
3. 先写 `backend/tests/test_runtime_config_store.py`，覆盖：
   - stable JSON hash 与 key order 无关。
   - `upsert()` 首次写入 revision 为 1。
   - 再次写入 revision 自增。
   - `content_hash` 跟随 payload 改变。
   - `load()` 返回独立 payload copy。
   - missing key 返回 `None`。
   - bootstrap env 默认 file mode，并支持 db mode。
4. 运行 focused test，确认红灯：
   - 首次 sandbox 内运行 `uv` 被 `~/.cache/uv` 权限限制阻塞。
   - 用已批准的 elevated pytest 命令重跑后，测试因 `deerflow.config.bootstrap` 不存在失败。
5. 实现最小代码：
   - `deerflow.config.bootstrap`
   - `deerflow.persistence.runtime_config.model`
   - `deerflow.persistence.runtime_config.sql`
   - `deerflow.persistence.runtime_config.__init__`
   - 注册 `RuntimeConfigRow`
6. 再次运行 focused test，发现 async 测试缺少 `pytest.mark.anyio`。
7. 补上 async test marker。
8. focused test 通过：`7 passed, 1 warning`。

### Files Changed

- `backend/tests/test_runtime_config_store.py`
- `backend/packages/harness/deerflow/config/bootstrap.py`
- `backend/packages/harness/deerflow/persistence/runtime_config/__init__.py`
- `backend/packages/harness/deerflow/persistence/runtime_config/model.py`
- `backend/packages/harness/deerflow/persistence/runtime_config/sql.py`
- `backend/packages/harness/deerflow/persistence/models/__init__.py`

### Verification

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py -q
```

Result:

```text
7 passed, 1 warning in 0.19s
```

The warning is an existing `LangChainPendingDeprecationWarning` from `langgraph.checkpoint.serde.encrypted`.

### Remaining Work

- Add `ConfigSource`/`DbConfigSource` on top of `RuntimeConfigRepository`.
- Decide whether bootstrap DB URL parsing should support only `DEER_FLOW_DATABASE_URL` or also structured backend/sqlite/postgres env.
- Add startup mismatch reporting for `runtime_configs.database` vs bootstrap DB settings.
- Add migration import into `runtime_configs`.
- Add broader persistence and lint verification after the next connected slice.

---

## Batch 2: ConfigSource Payload Layer

Date: 2026-06-19

### Goal

Introduce the source abstraction that later DB-backed `AppConfig` loading can use, without changing the existing `get_app_config()` file-mode path.

### Steps

1. Read `AppConfig.from_file()` and existing reload tests to identify the minimum source boundary.
2. Lock the first source scope to app runtime config only; extensions/MCP remain a separate future source to avoid dual ownership.
3. Write `backend/tests/test_config_sources.py` first, covering:
   - file source reads YAML into a `RevisionedPayload`.
   - file source computes `content_hash` using canonical JSON hash.
   - file source returns independent payload copies.
   - DB source reads key `app` from `RuntimeConfigRepository`.
   - DB source reports missing `app` config as `KeyError`.
4. Run focused test and confirm red:
   - `deerflow.config.sources` module did not exist.
5. Implement `deerflow.config.sources`:
   - `RevisionedPayload`
   - `FileConfigSource`
   - `DbConfigSource`
   - lightweight source protocols
6. Run focused source tests and confirm green.

### Files Changed

- `backend/tests/test_config_sources.py`
- `backend/packages/harness/deerflow/config/sources.py`

### Verification

```bash
uv --directory backend run pytest tests/test_config_sources.py -q
```

Result:

```text
4 passed, 1 warning in 0.18s
```

The warning is the existing `LangChainPendingDeprecationWarning` from `langgraph.checkpoint.serde.encrypted`.

### Remaining Work

- Add `AppConfig.from_source()` or an async DB config load path that validates `RevisionedPayload`.
- Decide the sync/async bridge for DB-backed config before wiring `get_app_config()`, because current `get_app_config()` is synchronous.
- Keep extensions/MCP out of `ConfigSource`; implement a dedicated `ExtensionsConfigSource` in a later batch.
- Add tests proving DB source does not become default unless bootstrap mode is `db`.

---

## Batch 3: AppConfig Payload Validation

Date: 2026-06-19

### Goal

Allow a `RevisionedPayload` from the source layer to be validated into `AppConfig`, while keeping the existing file-based `get_app_config()` behavior unchanged.

### Steps

1. Read `AppConfig.from_file()` to identify shared semantics:
   - env variable resolution
   - database defaults
   - extensions config merge
   - model validation
   - no-model warning
   - singleton config refresh
2. Add failing tests in `backend/tests/test_config_sources.py` for:
   - `AppConfig.from_payload()` applies env resolution and database defaults.
   - `from_payload()` does not mutate the caller payload.
   - explicit `ExtensionsConfig` is merged into `AppConfig`.
   - singleton memory config is refreshed.
   - `AppConfig.from_source(FileConfigSource(...))` validates a file source payload.
3. Run focused tests and confirm red:
   - `AppConfig.from_payload` and `AppConfig.from_source` did not exist.
4. Refactor `AppConfig.from_file()` so it still reads YAML and checks config version, then delegates shared validation to `from_payload()`.
5. Add `from_payload()` and synchronous `from_source()`.
6. Run focused source tests and confirm green.

### Files Changed

- `backend/packages/harness/deerflow/config/app_config.py`
- `backend/tests/test_config_sources.py`

### Verification

```bash
uv --directory backend run pytest tests/test_config_sources.py -q
```

Result:

```text
6 passed, 1 warning in 0.17s
```

### Remaining Work

- Add an async DB-mode composition path that awaits `DbConfigSource` and then calls `AppConfig.from_payload()`.
- Add source-aware cache invalidation for DB mode.
- Preserve current file `get_app_config()` reload behavior after the refactor with focused regression tests.
- Decide how startup-only field change metadata is surfaced for DB-backed config.

---

## Batch 4: Async DB AppConfig Cache Loader

Date: 2026-06-19

### Goal

Add the startup-time async bridge from `RuntimeConfigRepository` to cached `AppConfig`, without making synchronous `get_app_config()` perform database IO.

### Steps

1. Inspect current `AppConfig` cache globals and synchronous `get_app_config()` behavior.
2. Decide the V1 cache boundary:
   - DB mode startup must call an async loader.
   - After startup, synchronous callers only read the cached DB config.
   - If DB mode is enabled but no DB config has been preloaded, fail fast.
3. Add failing tests in `backend/tests/test_config_sources.py`:
   - DB mode `get_app_config()` raises before startup preload.
   - `load_and_cache_db_app_config()` loads `runtime_configs.app`.
   - after preload, `get_app_config()` returns the cached DB config.
   - a second preload after DB update refreshes the cached object.
4. Run focused tests and confirm red:
   - `load_and_cache_db_app_config` was missing.
5. Implement `load_and_cache_db_app_config()` in `deerflow.config.app_config`.
6. Add DB-mode guard in `get_app_config()`.
7. Track cache source kind as `file`, `db`, or `custom`.
8. Run focused tests and confirm green.

### Files Changed

- `backend/packages/harness/deerflow/config/app_config.py`
- `backend/tests/test_config_sources.py`

### Verification

```bash
uv --directory backend run pytest tests/test_config_sources.py -q
```

Result:

```text
9 passed, 1 warning in 0.19s
```

### Remaining Work

- Wire Gateway startup to initialize the persistence engine from bootstrap settings, create a repository, and call `load_and_cache_db_app_config()` when `DEER_FLOW_CONFIG_SOURCE=db`.
- Add a revision check helper so DB mode can detect config changes without restarting.
- Surface startup-only field changes as restart-required metadata.
- Implement `ExtensionsConfigSource` so DB-loaded AppConfig does not keep relying on file-backed extensions.

---

## Batch 5: Gateway DB Startup Preload

Date: 2026-06-19

### Goal

Connect the DB-backed AppConfig loader to Gateway startup, using bootstrap DB settings before file-backed `AppConfig` exists.

### Steps

1. Read `backend/app/gateway/app.py` lifespan and `backend/app/gateway/deps.py::langgraph_runtime()`.
2. Confirm the existing startup order:
   - `lifespan()` calls synchronous `get_app_config()`.
   - `langgraph_runtime()` initializes persistence from `startup_config.database`.
3. Write failing tests for:
   - `get_bootstrap_database_config()` derives `DatabaseConfig` from sqlite and postgres URLs.
   - DB-mode startup helper reads `runtime_configs.app` from the bootstrap DB.
   - DB-mode startup helper overrides runtime payload `database` with bootstrap database settings.
   - after startup preload, synchronous `get_app_config()` returns the cached DB config.
4. Run focused tests and confirm red:
   - `get_bootstrap_database_config()` was missing.
   - `_load_startup_config()` was missing.
5. Implement bootstrap URL parsing in `deerflow.config.bootstrap`.
6. Add `database_config` override support to `load_and_cache_db_app_config()`.
7. Add `app.gateway.app._load_startup_config()`:
   - file mode returns `get_app_config()`.
   - DB mode reads bootstrap DB config.
   - DB mode initializes persistence engine.
   - DB mode creates `RuntimeConfigRepository`.
   - DB mode preloads and caches AppConfig.
8. Change `lifespan()` to call `_load_startup_config()`.
9. Update `langgraph_runtime()` to reuse an already initialized persistence engine instead of initializing a second one.
10. Run focused startup tests and confirm green.

### Files Changed

- `backend/packages/harness/deerflow/config/bootstrap.py`
- `backend/packages/harness/deerflow/config/app_config.py`
- `backend/app/gateway/app.py`
- `backend/app/gateway/deps.py`
- `backend/tests/test_runtime_config_store.py`
- `backend/tests/test_gateway_db_config_startup.py`

### Verification

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py tests/test_gateway_db_config_startup.py -q
```

Result:

```text
10 passed, 1 warning in 0.53s
```

### Remaining Work

- Add a full Gateway lifespan smoke test for DB config mode.
- Implement DB-backed `ExtensionsConfigSource`; startup currently still loads extensions through `ExtensionsConfig.from_file()`.
- Make engine ownership explicit so startup preload and `langgraph_runtime()` shutdown responsibilities remain clear.
- Add revision-aware config reload or admin-triggered reload for DB mode.

---

## Batch 6: DB-backed ExtensionsConfigSource

Date: 2026-06-19

### Goal

Add the first DB-backed extensions view so DB-mode startup can load MCP and skill enabled state from DB instead of `extensions_config.json`.

### Steps

1. Re-check `ExtensionsConfig` behavior:
   - file-backed JSON supports `mcpServers` alias.
   - extensions are optional.
   - missing file currently means empty config.
2. Decide V1 source behavior for this batch:
   - `FileExtensionsConfigSource` mirrors existing JSON loading.
   - `DbExtensionsConfigSource` reads `runtime_configs.extensions`.
   - missing DB key returns empty extensions, matching optional file behavior.
3. Add failing tests for:
   - file source loads MCP and skill state from JSON.
   - DB source loads MCP and skill state from `runtime_configs.extensions`.
   - missing DB extensions key returns empty config with revision 0.
   - DB-mode startup injects DB extensions into cached `AppConfig`.
4. Run focused tests and confirm red:
   - `deerflow.config.extensions_sources` did not exist.
5. Implement `RevisionedExtensionsConfig`, `FileExtensionsConfigSource`, and `DbExtensionsConfigSource`.
6. Update Gateway DB startup helper to load extensions through `DbExtensionsConfigSource`.
7. Run focused tests and confirm green.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/app/gateway/app.py`
- `backend/tests/test_extensions_config_sources.py`
- `backend/tests/test_gateway_db_config_startup.py`

### Verification

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_gateway_db_config_startup.py -q
```

Result:

```text
5 passed, 1 warning in 0.55s
```

### Remaining Work

- Split MCP server config and skill enabled state into dedicated DB tables.
- Preserve MCP secret masking and masked round-trip semantics for DB writes.
- Replace runtime direct calls to `ExtensionsConfig.from_file()` with an extensions facade.
- Add revision/hash invalidation to MCP tool cache and session pool.

---

## Batch 7: DB-backed MemoryStorage

Date: 2026-06-19

### Goal

Add DB-backed memory persistence while preserving the current `MemoryStorage` synchronous API and the existing memory JSON shape.

### Steps

1. Inspect current memory code:
   - `MemoryStorage` is synchronous.
   - `FileMemoryStorage` uses `(user_id, agent_name)` scoped cache.
   - `save()` must not mutate caller data.
   - `lastUpdated` is set by storage on successful save.
2. Choose the V1 bridge strategy:
   - implement `DbMemoryStorage` using a synchronous SQLAlchemy engine/session.
   - avoid calling async SQLAlchemy from synchronous memory APIs.
   - keep async-first memory/API refactor out of this batch.
3. Add failing tests in `backend/tests/test_db_memory_storage.py` for:
   - missing row returns `create_empty_memory()`.
   - user-scoped save/load.
   - user + agent scope isolation.
   - `save()` does not mutate caller dict.
   - `reload()` bypasses local cache.
   - `get_memory_storage()` can load `DbMemoryStorage` by configured class path.
4. Run focused tests and confirm red:
   - `DbMemoryStorage` did not exist.
5. Add `memories` ORM model:
   - `owner_user_id`
   - `agent_scope`
   - `memory_json`
   - `schema_version`
   - `revision`
   - `last_updated`
   - timestamps
   - unique owner/scope constraint
6. Register `MemoryRow` in `deerflow.persistence.models`.
7. Implement `DbMemoryStorage` in `deerflow.agents.memory.storage`.
8. Export `DbMemoryStorage` from `deerflow.agents.memory`.
9. Run focused DB memory tests and existing memory storage/updater/prompt tests.

### Files Changed

- `backend/packages/harness/deerflow/persistence/memory/__init__.py`
- `backend/packages/harness/deerflow/persistence/memory/model.py`
- `backend/packages/harness/deerflow/persistence/models/__init__.py`
- `backend/packages/harness/deerflow/agents/memory/storage.py`
- `backend/packages/harness/deerflow/agents/memory/__init__.py`
- `backend/tests/test_db_memory_storage.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py tests/test_memory_storage.py tests/test_memory_storage_user_isolation.py tests/test_memory_updater.py tests/test_memory_updater_user_isolation.py tests/test_memory_prompt_injection.py -q
uv --directory backend run ruff check tests/test_db_memory_storage.py packages/harness/deerflow/agents/memory/storage.py packages/harness/deerflow/agents/memory/__init__.py packages/harness/deerflow/persistence/memory packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
104 passed, 1 warning in 4.78s
All checks passed!
```

### Remaining Work

- Add compare-and-swap revision handling for concurrent memory writes.
- Add migration from legacy memory files into `memories`.
- Add DB memory storage selection in imported runtime config examples.
- Keep recall behavior unchanged until a later explicit query/embedding recall phase.

---

## Batch 8: DB-backed Agent Store Foundation

Date: 2026-06-19

### Goal

Add DB-backed storage primitives for custom agents and user profiles while keeping existing file-based agent routers and tools untouched.

### Steps

1. Inspect current custom agent implementation:
   - per-user file layout under `users/{user_id}/agents/{name}`.
   - legacy shared fallback under `agents/{name}`.
   - `AgentConfig` stores name, description, model, tool groups, and skills.
   - `SOUL.md` stores agent personality text.
   - `USER.md` stores user profile text.
2. Add failing tests in `backend/tests/test_db_agent_store.py` for:
   - save/load agent config and soul.
   - preserving `skills=[]` distinct from `skills=None`.
   - user isolation and sorted listing.
   - update and delete behavior.
   - user profile load/save.
   - invalid agent names.
3. Run focused tests and confirm red:
   - `deerflow.config.agent_store` did not exist.
4. Implement persistence models:
   - `custom_agents`
   - `user_profiles`
5. Register agent/profile ORM models in `deerflow.persistence.models`.
6. Implement `DbAgentStore` with a synchronous SQLAlchemy engine/session.
7. Run focused tests and custom-agent regression subset.
8. Fix ruff import ordering in the model registry.

### Files Changed

- `backend/packages/harness/deerflow/persistence/agents/__init__.py`
- `backend/packages/harness/deerflow/persistence/agents/model.py`
- `backend/packages/harness/deerflow/config/agent_store.py`
- `backend/packages/harness/deerflow/persistence/models/__init__.py`
- `backend/tests/test_db_agent_store.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_custom_agent.py tests/test_create_deerflow_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py -q
uv --directory backend run ruff check tests/test_db_agent_store.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/persistence/agents packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
143 passed, 2 warnings in 0.75s
All checks passed!
```

### Remaining Work

- Wire `DbAgentStore` into agent routers when DB/stateless mode is enabled.
- Wire setup/update agent tools through `AgentStore`.
- Preserve legacy shared file fallback during migration.
- Add migration from per-user files and `USER.md` into DB tables.

---

## Batch 9: Mode-aware Agent Read Helpers

Date: 2026-06-19

### Goal

Route runtime custom-agent read helpers through DB storage when DB mode is enabled, while preserving file-mode behavior.

### Steps

1. Inspect current helper usage:
   - routers and lead-agent code call `load_agent_config()`, `load_agent_soul()`, and `list_custom_agents()`.
   - create/update/delete HTTP paths still write files directly.
2. Add failing test in `backend/tests/test_db_agent_store.py`:
   - seed `DbAgentStore`.
   - set `DEER_FLOW_CONFIG_SOURCE=db`.
   - inject an `AppConfig` carrying the test DB config.
   - assert `load_agent_config()`, `load_agent_soul()`, and `list_custom_agents()` read from DB.
3. Run focused test and confirm red:
   - `load_agent_config()` still attempted to read local files.
4. Refactor `agents_config.py`:
   - keep file logic in private `_load_agent_config_from_file()`, `_load_agent_soul_from_file()`, and `_list_custom_agents_from_file()`.
   - public helpers switch to `DbAgentStore` when DB config mode is enabled.
   - file mode continues using existing file layout and legacy fallback.
5. Run focused and agent regression tests.

### Files Changed

- `backend/packages/harness/deerflow/config/agents_config.py`
- `backend/tests/test_db_agent_store.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_custom_agent.py tests/test_create_deerflow_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py tests/test_lead_agent_prompt.py tests/test_lead_agent_model_resolution.py -q
uv --directory backend run ruff check tests/test_db_agent_store.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py packages/harness/deerflow/persistence/agents packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
179 passed, 2 warnings in 0.85s
All checks passed!
```

### Remaining Work

- Route HTTP create/update/delete through a store facade in DB mode.
- Route setup/update agent tools through the same facade.
- Add user profile router DB-mode behavior.
- Add migration from file-backed agents and profiles into DB.

---

## Batch 10: DB-mode Agent Write Surfaces

Date: 2026-06-19

### Goal

Route custom-agent write surfaces through DB storage when DB mode is enabled, while preserving existing file-mode behavior.

### Steps

1. Add failing DB-mode HTTP tests in `backend/tests/test_custom_agent.py`:
   - create, update, delete custom agent through `/api/agents`.
   - check duplicate-name behavior through `/api/agents/check`.
   - read/write user profile through `/api/user-profile`.
   - assert DB rows are written and local agent/USER.md files are not created.
2. Add failing DB-mode tool tests:
   - `setup_agent` creates a DB-backed custom agent without local files.
   - `update_agent` updates DB-backed config, skills, and soul without local files.
3. Fix a DB store prerequisite:
   - `DbAgentStore` now creates the SQLite parent directory before opening the sync engine.
4. Add active-store helper functions in `agents_config.py`:
   - `agent_config_exists()`
   - `save_agent_config()`
   - `delete_agent_config()`
   - `load_user_profile()`
   - `save_user_profile()`
5. Wire DB mode branches:
   - agent router create/update/delete/check use DB helper paths.
   - user profile router uses DB profile rows in DB mode.
   - `setup_agent` stores custom agents in DB in DB mode.
   - `update_agent` stores custom-agent updates in DB in DB mode.
6. Run focused and agent regression tests.

### Files Changed

- `backend/app/gateway/routers/agents.py`
- `backend/packages/harness/deerflow/config/agent_store.py`
- `backend/packages/harness/deerflow/config/agents_config.py`
- `backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py`
- `backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py`
- `backend/tests/test_custom_agent.py`
- `backend/tests/test_setup_agent_tool.py`
- `backend/tests/test_update_agent_tool.py`

### Verification

```bash
uv --directory backend run pytest tests/test_custom_agent.py::TestAgentsAPIDbMode tests/test_setup_agent_tool.py::TestSetupAgentNoDataLoss::test_db_mode_custom_agent_is_written_to_db_not_files tests/test_update_agent_tool.py::test_update_agent_db_mode_updates_db_not_files -q
uv --directory backend run pytest tests/test_custom_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py tests/test_db_agent_store.py tests/test_create_deerflow_agent.py tests/test_lead_agent_prompt.py tests/test_lead_agent_model_resolution.py -q
uv --directory backend run ruff check tests/test_custom_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py packages/harness/deerflow/tools/builtins/setup_agent_tool.py packages/harness/deerflow/tools/builtins/update_agent_tool.py app/gateway/routers/agents.py
```

Result:

```text
5 passed, 2 warnings in 0.53s
184 passed, 2 warnings in 0.92s
All checks passed!
```

### Remaining Work

- Add atomic create semantics for DB-backed custom agents to close the duplicate-create race.
- Add DB store lifecycle/caching instead of creating a sync engine per helper call.
- Add migration from existing per-user custom-agent files and `USER.md` into DB rows.
- Decide where default-agent global `SOUL.md` belongs in fully stateless mode.
- Route Skills and MCP state through DB-backed stores.

---

## Batch 11: DB-backed Custom Skill Storage

Date: 2026-06-19

### Goal

Add a DB-backed SkillStorage implementation that stores custom skill content and history in DB while materializing runtime files for existing prompt and sandbox consumers.

### Steps

1. Inspect the skill storage abstraction and consumers:
   - `SkillStorage.load_skills()` parses real `SKILL.md` paths.
   - slash activation, subagent execution, and skill tools still read `Skill.skill_file` directly.
   - sandbox providers mount `config.skills.get_skills_path()`.
2. Add failing tests in `backend/tests/test_db_skill_storage.py`:
   - custom skill persists to DB and rematerializes after cache deletion.
   - support files and history are stored and restored.
   - DB config mode defaults the skill storage factory to DB storage when the configured class is the default local implementation.
3. Add persistence models:
   - `custom_skills`
   - `custom_skill_history`
4. Implement `DbSkillStorage`:
   - custom skills are DB-backed.
   - public skills remain file-backed under the materialized root.
   - DB custom skills materialize to `custom/<name>/SKILL.md`.
   - history records are DB-backed and can be materialized to `.history/<name>.jsonl`.
5. Wire the factory:
   - DB config mode maps the default `LocalSkillStorage` class to `DbSkillStorage`.
   - explicit non-default storage classes remain respected.
6. Run focused skill tests and ruff.

### Files Changed

- `backend/packages/harness/deerflow/persistence/skills/__init__.py`
- `backend/packages/harness/deerflow/persistence/skills/model.py`
- `backend/packages/harness/deerflow/persistence/models/__init__.py`
- `backend/packages/harness/deerflow/skills/storage/__init__.py`
- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/tests/test_db_skill_storage.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skills_loader.py tests/test_skills_custom_router.py tests/test_skill_manage_tool.py tests/test_skills_parser.py -q
uv --directory backend run ruff check tests/test_db_skill_storage.py packages/harness/deerflow/skills/storage/db_skill_storage.py packages/harness/deerflow/skills/storage/__init__.py packages/harness/deerflow/persistence/skills packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
44 passed, 2 warnings in 0.68s
All checks passed!
```

### Remaining Work

- Add migration from local `skills/custom/**` into `custom_skills`.
- Support binary/custom asset storage instead of text-only support files.
- Route `skill_manage` remove-file operations through storage so DB state is updated.
- Add materialized-root lifecycle and cleanup policy.
- Update sandbox providers to consult the active storage root explicitly instead of only `config.skills.get_skills_path()`.

---

## Batch 12: DB-mode Extensions and MCP Runtime Updates

Date: 2026-06-19

### Goal

Route runtime MCP configuration reads and API writes through the DB-backed `runtime_configs.extensions` payload in DB mode, while preserving file-mode behavior and existing secret masking semantics.

### Steps

1. Inspect current MCP paths:
   - Gateway MCP GET uses `get_extensions_config()`.
   - Gateway MCP PUT writes `extensions_config.json`.
   - MCP tool loading calls `ExtensionsConfig.from_file()` directly.
   - ACP tool helpers also call `ExtensionsConfig.from_file()` directly.
2. Add failing tests:
   - sync DB extensions store round-trips `runtime_configs.extensions`.
   - MCP PUT in DB mode preserves masked secrets, preserves skills state and `mcpInterceptors`, writes DB, and does not resolve a file path.
3. Add `DbExtensionsConfigStore`:
   - synchronous SQLite/Postgres access to `runtime_configs.extensions`.
   - same revision/hash/update metadata semantics as the async repository.
4. Update `get_extensions_config()` and `reload_extensions_config()`:
   - DB mode reads from `DbExtensionsConfigStore`.
   - file mode keeps existing `ExtensionsConfig.from_file()` behavior.
5. Update runtime consumers:
   - MCP tool loading uses active-source `reload_extensions_config()`.
   - ACP MCP payload helpers use active-source reload.
   - skill enabled-state merge uses active-source reload.
6. Update MCP PUT router:
   - DB mode preserves masked secrets using existing merge logic.
   - DB mode preserves top-level extras and current skills state.
   - DB mode saves the updated `ExtensionsConfig` to `runtime_configs.extensions`.
   - DB mode reloads extensions cache and resets MCP tools cache.
7. Run MCP/Extensions regression tests and ruff.

### Files Changed

- `backend/app/gateway/routers/mcp.py`
- `backend/packages/harness/deerflow/config/extensions_config.py`
- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/packages/harness/deerflow/mcp/tools.py`
- `backend/packages/harness/deerflow/skills/storage/skill_storage.py`
- `backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py`
- `backend/packages/harness/deerflow/tools/tools.py`
- `backend/tests/test_extensions_config_sources.py`
- `backend/tests/test_mcp_config_secrets.py`

### Verification

```bash
uv --directory backend run pytest tests/test_mcp_config_secrets.py tests/test_extensions_config_sources.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py -q
uv --directory backend run ruff check tests/test_extensions_config_sources.py tests/test_mcp_config_secrets.py packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/extensions_config.py app/gateway/routers/mcp.py packages/harness/deerflow/mcp/tools.py packages/harness/deerflow/tools/tools.py packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py packages/harness/deerflow/skills/storage/skill_storage.py
```

Result:

```text
94 passed, 1 warning in 0.83s
All checks passed!
```

### Remaining Work

- Route skill enabled toggles through the same DB save path in DB mode.
- Add optimistic revision checks for concurrent MCP updates.
- Add cross-process MCP cache invalidation; current reset is process-local.
- Add audit metadata for updated_by from authenticated admin identity everywhere.
- Decide whether MCP config should stay in `runtime_configs.extensions` or split into dedicated MCP tables before production migration.

---

## Batch 13: DB-mode Skill Enabled Toggle

Date: 2026-06-19

### Goal

Route `/api/skills/{skill_name}` enabled-state updates through the DB-backed extensions payload in DB mode.

### Steps

1. Add a failing router test:
   - seed `runtime_configs.extensions` with MCP servers and top-level extras.
   - enable DB config mode.
   - assert PUT `/api/skills/demo-skill` does not resolve a file path.
   - assert DB `skills.demo-skill.enabled` is updated.
   - assert MCP servers and `mcpInterceptors` are preserved.
2. Add a DB branch in `app/gateway/routers/skills.py`:
   - read current extensions config from active cache.
   - update only the requested skill state.
   - save through `DbExtensionsConfigStore`.
   - reload extensions config and refresh the skill prompt cache.
3. Run targeted and related regression tests.

### Files Changed

- `backend/app/gateway/routers/skills.py`
- `backend/tests/test_skills_custom_router.py`

### Verification

```bash
uv --directory backend run pytest tests/test_skills_custom_router.py tests/test_mcp_config_secrets.py tests/test_extensions_config_sources.py tests/test_db_skill_storage.py -q
uv --directory backend run ruff check tests/test_skills_custom_router.py app/gateway/routers/skills.py
```

Result:

```text
45 passed, 2 warnings in 0.68s
All checks passed!
```

### Remaining Work

- Add compare-and-swap revision checks for the shared `extensions` payload.
- Add a common helper for extensions payload update/merge to reduce duplicate MCP/skills router code.
- Add cross-process cache invalidation for skill prompt caches.

---

## Batch 14: DB-mode Memory Storage Selection

Date: 2026-06-19

### Goal

Make DB mode select `DbMemoryStorage` by default so memory writes and recalls use DB storage without requiring explicit memory storage_class overrides.

### Steps

1. Add failing tests in `backend/tests/test_db_memory_storage.py`:
   - `DbMemoryStorage` creates a missing SQLite parent directory.
   - DB config mode maps the default file memory storage class to `DbMemoryStorage`.
2. Update `DbMemoryStorage`:
   - create SQLite parent directory before opening the sync engine.
3. Update `get_memory_storage()`:
   - when `DEER_FLOW_CONFIG_SOURCE=db` and memory storage class is the default file storage, instantiate `DbMemoryStorage`.
   - non-default custom storage classes remain routed through the configured class path.
4. Run memory regression tests and ruff.

### Files Changed

- `backend/packages/harness/deerflow/agents/memory/storage.py`
- `backend/tests/test_db_memory_storage.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py tests/test_memory_storage.py tests/test_memory_storage_user_isolation.py tests/test_memory_updater.py tests/test_memory_updater_user_isolation.py tests/test_memory_prompt_injection.py -q
uv --directory backend run ruff check tests/test_db_memory_storage.py packages/harness/deerflow/agents/memory/storage.py
```

Result:

```text
106 passed, 1 warning in 0.46s
All checks passed!
```

### Remaining Work

- Add compare-and-swap revision checks for concurrent memory updates.
- Add migration from file-backed memory JSON into `memories`.
- Add multi-process cache invalidation or revision checks in `DbMemoryStorage.load()`.

---

## Batch 15: DB-mode Skill Support-file Delete

Date: 2026-06-19

### Goal

Make `skill_manage remove_file` delete support files from the DB source of truth instead of only deleting the materialized cache file.

### Steps

1. Add failing tests:
   - `DbSkillStorage.delete_custom_skill_file()` removes a support file from DB and does not rematerialize it after cache deletion.
   - `skill_manage remove_file` removes the DB backing record and still writes history with the previous file content.
2. Add `SkillStorage.delete_custom_skill_file()` as a storage-level operation:
   - default implementation keeps local filesystem behavior by validating the support path and unlinking the file.
3. Override `DbSkillStorage.delete_custom_skill_file()`:
   - validate skill name and support-file path.
   - remove the normalized path from `files_json`.
   - increment `revision`.
   - unlink the current materialized file if present.
4. Route `skill_manage remove_file` through the storage method instead of calling `Path.unlink()` directly.

### Files Changed

- `backend/packages/harness/deerflow/skills/storage/skill_storage.py`
- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/packages/harness/deerflow/tools/skill_manage_tool.py`
- `backend/tests/test_db_skill_storage.py`
- `backend/tests/test_skill_manage_tool.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skill_manage_tool.py -q
```

Result:

```text
10 passed, 1 warning in 0.32s
```

### Remaining Work

- Add DB support for binary skill assets instead of storing all support files as text.
- Add stale-file pruning when materializing a DB skill into a dirty cache directory.
- Route sandbox skill mounts through active storage rather than reading only configured filesystem paths.

---

## Batch 16: DB-mode Skill Materialization Pruning

Date: 2026-06-19

### Goal

Keep DB-backed skill materialization aligned with the DB snapshot so stale files in the host cache are not mounted into AIOSandbox.

### Steps

1. Add failing DB storage tests:
   - stale support files under an existing custom skill are pruned on rematerialization.
   - custom skill directories that no longer have a DB row are pruned during `load_skills()`.
2. Promote the allowed support-file top-level directory set into `ALLOWED_SUPPORT_SUBDIRS`.
3. Update `DbSkillStorage._materialize_row()`:
   - prune allowed support subdirectories before writing the current DB file set.
   - rewrite `SKILL.md` and DB-backed support files from the row snapshot.
4. Update `DbSkillStorage._iter_skill_files()`:
   - list DB rows once.
   - prune stale custom skill directories except `.history`.
   - materialize and yield only active DB-backed custom skills.

### Files Changed

- `backend/packages/harness/deerflow/skills/storage/skill_storage.py`
- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/tests/test_db_skill_storage.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py -q
```

Result:

```text
6 passed, 1 warning in 0.19s
```

### Remaining Work

- Add multi-process cache freshness tests with separate storage instances and shared DB/cache roots.
- Add typed binary asset storage and materialization.
- Route sandbox mounts through active storage so the cache root is no longer inferred from config path alone.

---

## Batch 17: Sandbox Active Skill Mount Root

Date: 2026-06-19

### Goal

Make LocalSandbox and AIO sandbox skill mounts use the active `SkillStorage` root so DB-backed skills are materialized before being mounted into sandbox runtime.

### Steps

1. Add failing tests:
   - `LocalSandboxProvider` should map `/mnt/skills` to `get_or_new_skill_storage(app_config=config).get_skills_root_path()`.
   - `AioSandboxProvider._get_skills_mount()` should use the same active storage root when computing the read-only skills mount.
2. Update `LocalSandboxProvider._setup_path_mappings()`:
   - keep `config.skills.container_path` as the container mount target.
   - use active skill storage root as the local source path.
3. Update `AioSandboxProvider._get_skills_mount()`:
   - use active skill storage root before applying `DEER_FLOW_HOST_SKILLS_PATH` host-side override.
   - preserve read-only skill mount behavior.

### Files Changed

- `backend/packages/harness/deerflow/sandbox/local/local_sandbox_provider.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/tests/test_local_sandbox_provider_mounts.py`
- `backend/tests/test_aio_sandbox_provider.py`

### Verification

```bash
uv --directory backend run pytest tests/test_local_sandbox_provider_mounts.py::TestLocalSandboxProviderMounts::test_setup_path_mappings_uses_active_skill_storage_root tests/test_aio_sandbox_provider.py::test_get_skills_mount_uses_active_skill_storage_root -q
```

Result:

```text
2 passed, 1 warning in 0.16s
```

### Remaining Work

- Add an integration test using a real `DbSkillStorage` and sandbox provider to assert a DB-backed skill file is readable through `/mnt/skills`.
- Define host-path translation for DB materialized skills in Docker-in-Docker deployments where `DEER_FLOW_HOST_SKILLS_PATH` may need to point at the materialized cache root.
- Extend the same source-of-truth contract to any future memory/profile mounts if those become filesystem-mounted into sandbox.

---

## Batch 18: LocalSandbox DB Skill Integration

Date: 2026-06-19

### Goal

Add an end-to-end regression check that a DB-backed custom skill is materialized and readable through LocalSandbox at `/mnt/skills`.

### Steps

1. Build a real DB-mode `AppConfig` with:
   - SQLite runtime DB.
   - default local skill storage class, which DB mode maps to `DbSkillStorage`.
   - `LocalSandboxProvider`.
2. Write a DB-backed custom skill through `get_or_new_skill_storage(app_config=app_config)`.
3. Instantiate `LocalSandboxProvider` from the same active app config.
4. Acquire the local sandbox and read `/mnt/skills/custom/db-skill/SKILL.md`.
5. Assert the content matches the DB-backed skill payload.

### Files Changed

- `backend/tests/test_local_sandbox_provider_mounts.py`

### Verification

```bash
uv --directory backend run pytest tests/test_local_sandbox_provider_mounts.py::TestLocalSandboxProviderMounts::test_local_sandbox_reads_db_materialized_custom_skill -q
```

Result:

```text
1 passed, 1 warning in 0.17s
```

### Remaining Work

- Add the equivalent AIO/Docker smoke test once a container-backed test harness is available.
- Define deployment documentation for `DEER_FLOW_HOST_SKILLS_PATH` in DB mode.

---

## Batch 19: DB Memory Revision Freshness

Date: 2026-06-19

### Goal

Close the DB memory P1 gaps where cached memory could become stale across processes and stale saves could overwrite newer DB rows.

### Steps

1. Update DB memory tests:
   - `load()` should refresh from DB when another storage instance advances the row revision.
   - `save()` should reject a stale cached write when the DB row revision has changed.
2. Add `DbMemoryStorage._load_revision()` to cheaply read the current DB revision.
3. Update `DbMemoryStorage.load()`:
   - keep returning cached memory when the cached revision matches DB.
   - reload the full row when the DB revision differs.
4. Update `DbMemoryStorage.save()`:
   - read the expected revision from the instance cache.
   - reject saves when the DB row revision no longer matches the cached revision.
   - preserve upsert behavior for uncached saves.

### Files Changed

- `backend/packages/harness/deerflow/agents/memory/storage.py`
- `backend/tests/test_db_memory_storage.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py -q
```

Result:

```text
9 passed, 1 warning in 0.28s
```

### Remaining Work

- Surface stale-save failures to the memory updater so it can reload/merge/retry instead of silently dropping an update.
- Add row-level locking or database-native compare-and-swap for high-contention memory writes.
- Add migration from file-backed memory JSON into `memories`.

---

## Batch 20: Memory Updater Stale-save Retry

Date: 2026-06-19

### Goal

Make the memory updater handle storage-level stale-save failures by reloading the latest memory, reapplying the model update, and retrying once.

### Steps

1. Add a failing updater test:
   - first `save()` returns `False`.
   - updater calls `reload()` for the latest memory.
   - updater reapplies the same parsed update to the fresh memory.
   - second `save()` succeeds.
2. Update `_finalize_update()`:
   - parse model response once.
   - apply and save against the prompt-time memory.
   - on failed save, reload latest memory and reapply the same update once.
3. Update existing cache-isolation test expectations:
   - save failure now attempts two saves.
   - original cached memory still must not be mutated.

### Files Changed

- `backend/packages/harness/deerflow/agents/memory/updater.py`
- `backend/packages/harness/deerflow/agents/memory/__init__.py`
- `backend/tests/test_memory_updater.py`

### Verification

```bash
uv --directory backend run pytest tests/test_memory_updater.py -q
```

Result:

```text
54 passed, 1 warning in 0.31s
```

### Remaining Work

- Consider a configurable retry count for high-contention memory workloads.
- Add conflict-specific telemetry so stale-save retry can be distinguished from storage I/O failures.
- Add a DB integration test where `DbMemoryStorage.save()` returns `False` due to revision mismatch and updater retry succeeds.

---

## Batch 21: DB Extensions Revision Guard

Date: 2026-06-19

### Goal

Prevent last-write-wins overwrites when MCP configuration and skill enabled toggles update the shared DB-backed `runtime_configs.extensions` payload.

### Steps

1. Add failing tests:
   - `DbExtensionsConfigStore` rejects saves when `expected_revision` is stale.
   - MCP DB-mode PUT returns HTTP 409 when the extensions revision changes during update.
2. Add `ExtensionsConfigConflictError`.
3. Extend `DbExtensionsConfigStore.save_extensions_config()`:
   - accept optional `expected_revision`.
   - reject stale revision for both missing-row and existing-row cases.
4. Update MCP DB-mode config PUT:
   - load current config and revision from the DB store.
   - preserve secrets and extras.
   - save with `expected_revision`.
   - return 409 on conflict.
5. Update skill enabled DB-mode toggle:
   - load current config and revision from the DB store.
   - save with `expected_revision`.
   - return 409 on conflict.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/app/gateway/routers/mcp.py`
- `backend/app/gateway/routers/skills.py`
- `backend/tests/test_extensions_config_sources.py`
- `backend/tests/test_mcp_config_secrets.py`

### Verification

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_config_secrets.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py tests/test_skills_custom_router.py -q
```

Result:

```text
106 passed, 2 warnings in 0.92s
```

### Remaining Work

- Add cross-process cache invalidation so other gateway processes notice committed extensions revisions without manual cache reset.
- Factor duplicated MCP/skills extensions payload merge code into a shared helper.
- Add conflict telemetry and frontend retry guidance for 409 responses.

---

## Batch 22: DB Extensions Cache Freshness

Date: 2026-06-19

### Goal

Make `get_extensions_config()` refresh automatically in DB mode when `runtime_configs.extensions` revision changes.

### Steps

1. Add a failing cache freshness test:
   - seed DB extensions config with one MCP server.
   - call `get_extensions_config()` to warm the singleton cache.
   - update the DB row through another store instance.
   - call `get_extensions_config()` again and expect the newer revision.
2. Add `_extensions_revision` alongside `_extensions_config`.
3. Add `_load_db_extensions_config_revisioned()`:
   - reuse DB bootstrap/AppConfig resolution.
   - return the revisioned DB payload from `DbExtensionsConfigStore`.
4. Update `get_extensions_config()`:
   - in DB mode, load current revision and replace the cached config when revision changes.
   - preserve file-mode singleton behavior.
5. Update `reload_extensions_config()`, `reset_extensions_config()`, and `set_extensions_config()` to keep revision state consistent.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_config.py`
- `backend/tests/test_extensions_config_sources.py`

### Verification

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_config_secrets.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py tests/test_skills_custom_router.py tests/test_skills_loader.py tests/test_app_config_reload.py -q
```

Result:

```text
124 passed, 2 warnings in 1.05s
```

### Remaining Work

- Add revision-aware MCP tools cache invalidation, not just extensions config singleton freshness.
- Add revision-aware skill prompt cache invalidation across processes.
- Consider reducing DB reads with a short TTL or pub/sub invalidation if `get_extensions_config()` becomes hot.

---

## Batch 23: MCP Tools Cache Revision Invalidation

Date: 2026-06-19

### Goal

Make MCP tools cache stale detection work in DB mode by tracking `runtime_configs.extensions` revision in addition to file mtime.

### Steps

1. Add a failing MCP cache test:
   - initialize cache state with DB extensions revision 1.
   - update DB extensions config to revision 2.
   - assert `_is_cache_stale()` returns `True`.
2. Add `get_extensions_config_revision()` to expose the active DB revision.
3. Update MCP cache:
   - add `_config_revision`.
   - record revision during `initialize_mcp_tools()`.
   - treat revision changes as stale in `_is_cache_stale()`.
   - reset revision in `reset_mcp_tools_cache()`.
4. Preserve existing file-mode mtime stale detection.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_config.py`
- `backend/packages/harness/deerflow/mcp/cache.py`
- `backend/tests/test_mcp_cache_revision.py`

### Verification

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py tests/test_mcp_config_secrets.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py tests/test_extensions_config_sources.py -q
```

Result:

```text
98 passed, 1 warning in 0.87s
```

### Remaining Work

- Add an end-to-end test where `get_cached_mcp_tools()` resets and reloads after DB revision changes.
- Add revision-aware invalidation for skill prompt cache.
- Add telemetry for cache invalidation reason: file mtime vs DB revision.

---

## Batch 24: Skill Prompt Cache Revision Invalidation

Date: 2026-06-19

### Goal

Make enabled-skill prompt cache refresh when DB-backed extensions config revision changes.

### Steps

1. Add a failing prompt cache test:
   - warm enabled skills at revision 1.
   - change extensions revision to 2 and change the storage result.
   - call warm again and expect the new skill list.
2. Track `_enabled_skills_extensions_revision` beside `_enabled_skills_cache`.
3. Update refresh worker:
   - load enabled skills.
   - record the extensions revision associated with the loaded skill state.
4. Update `_ensure_enabled_skills_cache()`:
   - compare cached revision with current extensions revision.
   - invalidate skill prompt cache and start a refresh when revision changes.
5. Keep explicit cache clear/reload behavior intact.

### Files Changed

- `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`
- `backend/tests/test_lead_agent_prompt.py`

### Verification

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py tests/test_lead_agent_skills.py tests/test_skills_loader.py tests/test_skills_custom_router.py tests/test_extensions_config_sources.py -q
```

Result:

```text
53 passed, 2 warnings in 0.70s
```

### Remaining Work

- Add a true DB integration test that toggles a skill enabled state in DB and verifies prompt section refreshes without explicit reset.
- Consider TTL/pub-sub if revision checks become too frequent.

---

## Batch 25: DB Skill Binary Asset Storage

Date: 2026-06-19

### Goal

Allow DB-backed custom skills installed from `.skill` archives to preserve binary support assets such as images or binary templates.

### Steps

1. Add a failing DB storage test:
   - create a `.skill` archive with `SKILL.md` and `assets/logo.bin`.
   - install it through `DbSkillStorage`.
   - remove the materialized cache.
   - rematerialize from DB and assert the binary bytes are unchanged.
2. Add typed support-file serialization in `DbSkillStorage`:
   - UTF-8 text support files remain stored as plain strings for backward compatibility.
   - non-UTF-8 support files are stored as JSON payloads with `encoding: base64`.
3. Update materialization:
   - write base64 payloads as bytes.
   - keep existing string payloads as UTF-8 text.
4. Update archive installation:
   - read support files as bytes.
   - store as text only when UTF-8 decoding succeeds.
   - otherwise store as binary base64 payload.

### Files Changed

- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/tests/test_db_skill_storage.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skill_manage_tool.py tests/test_skills_installer.py tests/test_skills_archive_root.py tests/test_skills_loader.py tests/test_skills_custom_router.py -q
```

Result:

```text
63 passed, 2 warnings in 0.78s
```

### Remaining Work

- Preserve executable permission metadata for script support files installed from archives.
- Consider splitting support files into a dedicated table if JSON payload size becomes a concern.

---

## Batch 26: DB Skill Support-file Mode Metadata

Date: 2026-06-19

### Goal

Allow DB-backed custom skills installed from `.skill` archives to preserve support-file Unix mode metadata, especially executable bits for script assets.

### Steps

1. Add a failing DB storage test:
   - create a `.skill` archive with `scripts/run.sh`.
   - set the zip entry mode to `0755` via `ZipInfo.external_attr`.
   - install through `DbSkillStorage`.
   - delete the materialized cache.
   - rematerialize from DB and assert the script remains executable.
2. Read archive entry mode metadata before safe extraction.
3. Extend DB support-file payloads:
   - plain text without metadata remains stored as a string for backward compatibility.
   - binary payloads can carry `mode`.
   - text payloads with metadata use `encoding: utf-8`, `text`, and `mode`.
4. Update materialization:
   - restore base64 bytes or UTF-8 text based on payload encoding.
   - apply stored `mode` after writing the file.
5. Preserve existing support-file mode metadata when rewriting an already stored file without an explicit new mode.

### Files Changed

- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/tests/test_db_skill_storage.py`

### Verification

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py::test_db_skill_storage_preserves_script_executable_mode_from_archive -q
```

Result:

```text
1 passed, 1 warning in 0.17s
```

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skill_manage_tool.py tests/test_skills_installer.py tests/test_skills_archive_root.py tests/test_skills_loader.py tests/test_skills_custom_router.py -q
```

Result:

```text
64 passed, 2 warnings in 0.80s
```

```bash
uv --directory backend run ruff check tests/test_db_skill_storage.py packages/harness/deerflow/skills/storage/db_skill_storage.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Consider splitting support-file content and metadata into a dedicated DB table if binary assets or script packages become large.
- Add a fixture for archive-root path variants if future `.skill` packages support multiple root layouts beyond the current installer contract.

---

## Batch 27: MCP Server DB Store Foundation

Date: 2026-06-19

### Goal

Start moving MCP configuration from aggregate `runtime_configs.extensions` payloads toward first-class `mcp_servers` DB rows, while preserving the existing `ExtensionsConfig` view shape.

### Steps

1. Add failing MCP DB store tests:
   - load an `ExtensionsConfig` view from independent MCP server rows.
   - preserve env, headers, OAuth secrets, description, args, and extra fields.
   - reject stale full saves using revision/hash checks.
2. Add `mcp_servers` ORM model:
   - one row per MCP server.
   - typed columns for transport, command, args, url, env, headers, OAuth, description, revision, hash, and updater.
   - `extra_json` for forward-compatible server fields.
3. Add `DbMcpServerStore`:
   - full-load into `RevisionedExtensionsConfig`.
   - full-save with stale revision/hash conflict checks.
   - preserve raw secret payloads in DB.
4. Register `McpServerRow` in the ORM model registry.

### Files Changed

- `backend/packages/harness/deerflow/config/mcp_store.py`
- `backend/packages/harness/deerflow/persistence/mcp/__init__.py`
- `backend/packages/harness/deerflow/persistence/mcp/model.py`
- `backend/packages/harness/deerflow/persistence/models/__init__.py`
- `backend/tests/test_mcp_db_store.py`

### Verification

```bash
uv --directory backend run pytest tests/test_mcp_db_store.py -q
```

Result:

```text
2 passed, 1 warning in 0.17s
```

```bash
uv --directory backend run pytest tests/test_mcp_db_store.py tests/test_mcp_config_secrets.py tests/test_mcp_cache_revision.py tests/test_extensions_config_sources.py -q
```

Result:

```text
38 passed, 1 warning in 0.68s
```

```bash
uv --directory backend run ruff check tests/test_mcp_db_store.py packages/harness/deerflow/config/mcp_store.py packages/harness/deerflow/persistence/mcp/model.py packages/harness/deerflow/persistence/models/__init__.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Wire DB-mode MCP API and runtime readers to `DbMcpServerStore` instead of writing MCP servers only into `runtime_configs.extensions`.
- Decide whether aggregate MCP revision should get a dedicated monotonic meta row before cross-process cache invalidation depends on it.
- Add migration from existing `runtime_configs.extensions.mcpServers` rows into the new `mcp_servers` table.

---

## Batch 28: MCP DB Store Integration Through Extensions View

Date: 2026-06-19

### Goal

Make `DbExtensionsConfigStore` use the first-class `mcp_servers` table for MCP server state while preserving the existing `ExtensionsConfig` view consumed by API and runtime paths.

### Steps

1. Add a failing extensions-store test:
   - save an extensions config containing MCP, skills, and extra fields.
   - assert MCP server secrets are stored in `mcp_servers`.
   - assert `runtime_configs.extensions` no longer stores `mcpServers`.
   - assert loading still returns the original `ExtensionsConfig` view.
2. Update `DbExtensionsConfigStore.load_extensions_config()`:
   - load MCP rows from `DbMcpServerStore`.
   - combine MCP rows with runtime-config skills and extra fields.
   - keep legacy aggregate `mcpServers` fallback when the MCP table is empty.
3. Update `DbExtensionsConfigStore.save_extensions_config()`:
   - split `mcpServers` from the aggregate payload.
   - save MCP servers through `DbMcpServerStore`.
   - persist only skills and extra fields in `runtime_configs.extensions`.
4. Preserve the existing runtime revision guard on the extensions aggregate row so DB-mode API writes still reject stale updates.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/tests/test_extensions_config_sources.py`

### Verification

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py::test_db_extensions_config_store_writes_mcp_servers_to_dedicated_rows -q
```

Result:

```text
1 passed, 1 warning in 0.18s
```

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_config_secrets.py tests/test_mcp_cache_revision.py tests/test_mcp_client_config.py tests/test_mcp_oauth.py tests/test_mcp_custom_interceptors.py tests/test_mcp_session_pool.py tests/test_mcp_sync_wrapper.py -q
```

Result:

```text
101 passed, 1 warning in 0.94s
```

```bash
uv --directory backend run ruff check tests/test_extensions_config_sources.py tests/test_mcp_db_store.py packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/mcp_store.py packages/harness/deerflow/persistence/mcp/model.py packages/harness/deerflow/persistence/models/__init__.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Add a migration command to move legacy `runtime_configs.extensions.mcpServers` payloads into `mcp_servers`.
- Add explicit tests for legacy aggregate fallback when no MCP rows exist.
- Revisit aggregate revision shape before direct writes to `DbMcpServerStore` are used outside `DbExtensionsConfigStore`.

---

## Batch 29: Runtime State Import Dry-run Inventory

Date: 2026-06-19

### Goal

Add the first migration command slice: a default dry-run inventory that reports file-backed runtime state and import risks without writing DB rows.

### Steps

1. Add failing dry-run tests:
   - seed `config.yaml`, `extensions_config.json`, MCP servers, skill states, agents, memory files, and skills.
   - collect inventory and assert all source categories are reported.
   - report MCP stdio statefulness and resolved secret risks.
   - verify dry-run does not create a SQLite DB file.
2. Add `backend/scripts/import_runtime_state_to_db.py`:
   - scan app config and extensions config.
   - scan legacy and per-user agents.
   - scan global, user, and agent memory files.
   - scan public/custom skills and support file counts.
   - produce a structured JSON-compatible report.
3. Add a CLI wrapper:
   - default mode is dry-run.
   - `--apply` is explicit but currently raises `NotImplementedError`.
   - no DB store is initialized during dry-run.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
2 passed, 1 warning in 0.16s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_migration_user_isolation.py -q
```

Result:

```text
15 passed, 1 warning in 0.19s
```

```bash
uv --directory backend run ruff check tests/test_import_runtime_state_to_db.py scripts/import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Implement `--apply` for config/extensions/MCP import.
- Add `--apply` for agents, user profiles, memory, and skills using existing DB stores.
- Add migration support for legacy `runtime_configs.extensions.mcpServers` rows into `mcp_servers`.
- Include schema validation failures in the dry-run report instead of only reporting parse errors.

---

## Batch 30: Runtime State Import Apply For Config, Extensions, And MCP

Date: 2026-06-19

### Goal

Implement the first mutating import path: `--apply` writes app config, extensions skill state, and MCP servers into DB-backed stores.

### Steps

1. Add a failing apply test:
   - seed `config.yaml` and `extensions_config.json`.
   - run `import_runtime_state_to_db(..., apply=True)`.
   - assert `runtime_configs.app` is written.
   - assert `runtime_configs.extensions` stores skill state without `mcpServers`.
   - assert MCP servers are written to `mcp_servers`.
2. Add sync runtime-config upsert helper for the migration script.
3. Add config/extensions apply logic:
   - app config YAML goes to `runtime_configs.app`.
   - extensions JSON is validated as `ExtensionsConfig`.
   - extensions/MCP write through `DbExtensionsConfigStore`, preserving the MCP table split.
4. Keep dry-run non-mutating and preserve the structured inventory report.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_config_extensions_and_mcp -q
```

Result:

```text
1 passed, 1 warning in 0.17s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_config_secrets.py -q
```

Result:

```text
50 passed, 1 warning in 0.77s
```

```bash
uv --directory backend run ruff check tests/test_import_runtime_state_to_db.py scripts/import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Implement `--apply` for agents and user profiles through `DbAgentStore`.
- Implement `--apply` for memory through `DbMemoryStorage`.
- Implement `--apply` for skills through `DbSkillStorage`.
- Add schema validation details to dry-run before applying.

---

## Batch 31: Runtime State Import Apply For Agents And User Profiles

Date: 2026-06-19

### Goal

Extend the runtime state migration apply path to import `USER.md` and custom agents into DB-backed agent stores.

### Steps

1. Add failing tests:
   - inventory reports global `USER.md`.
   - apply writes `USER.md` into `DbAgentStore` as the default user profile.
   - apply writes legacy agents as owner `default`.
   - apply writes per-user agents under their user id.
2. Extend inventory:
   - add `user_profiles`.
   - include `summary.user_profiles`.
3. Extend apply:
   - save profiles through `DbAgentStore.save_user_profile()`.
   - parse agent `config.yaml`.
   - read optional `SOUL.md`.
   - save agents through `DbAgentStore.save_agent()`.
4. Preserve dry-run safety and existing config/extensions/MCP apply behavior.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
4 passed, 1 warning in 0.19s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_db_agent_store.py tests/test_custom_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py -q
```

Result:

```text
106 passed, 2 warnings in 0.90s
```

```bash
uv --directory backend run ruff check tests/test_import_runtime_state_to_db.py scripts/import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Implement `--apply` for memory through `DbMemoryStorage`.
- Implement `--apply` for skills through `DbSkillStorage`.
- Add conflict policy for duplicate legacy and per-user agents with the same owner/name.
- Add dry-run validation errors for invalid agent YAML before apply.

---

## Batch 32: Runtime State Import Apply For Memory

Date: 2026-06-19

### Goal

Extend the runtime state migration apply path to import global, user-level, and agent-scoped memory files into `DbMemoryStorage`.

### Steps

1. Add a failing memory apply test:
   - seed legacy global `memory.json`.
   - seed legacy agent memory.
   - seed per-user memory.
   - seed per-user agent memory.
   - assert all four scopes are readable from `DbMemoryStorage` after apply.
2. Extend apply:
   - read each inventory memory JSON file.
   - map legacy global memory to owner `default`, empty agent scope.
   - map legacy agent memory to owner `default`, agent scope.
   - map per-user memory to that user, empty agent scope.
   - map per-user agent memory to that user and agent scope.
3. Reuse `DbMemoryStorage.save()` so revision and `lastUpdated` behavior stays centralized.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
5 passed, 1 warning in 0.29s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_db_memory_storage.py tests/test_memory_updater.py tests/test_memory_prompt_injection.py -q
```

Result:

```text
79 passed, 1 warning in 0.50s
```

```bash
uv --directory backend run ruff check tests/test_import_runtime_state_to_db.py scripts/import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Implement `--apply` for skills through `DbSkillStorage`.
- Add dry-run validation for invalid memory JSON shape.
- Add conflict policy for importing memory over existing DB rows.

---

## Batch 33: Runtime State Import Apply For Custom Skills

Date: 2026-06-19

### Goal

Complete the migration apply path for V1 runtime state by importing custom skills into `DbSkillStorage`.

### Steps

1. Add a failing custom skill apply test:
   - seed one custom skill with `SKILL.md` and a support file.
   - seed one public skill.
   - run `import_runtime_state_to_db(..., apply=True)`.
   - assert only the custom skill is imported into DB storage.
   - rematerialize the custom skill from DB into a separate cache and assert support files survive.
2. Implement custom skill apply:
   - use `DbSkillStorage`.
   - write `SKILL.md`.
   - write support files as UTF-8 text or bytes.
   - preserve support file mode metadata.
3. Use a temporary materialization root during import so source `skills_root` is never pruned or mutated by DB cache synchronization.
4. Keep public skills as file-backed/read-only for V1.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
6 passed, 1 warning in 0.31s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_db_skill_storage.py tests/test_skill_manage_tool.py tests/test_skills_installer.py tests/test_skills_loader.py tests/test_skills_custom_router.py -q
```

Result:

```text
67 passed, 2 warnings in 0.93s
```

```bash
uv --directory backend run ruff check tests/test_import_runtime_state_to_db.py scripts/import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Add dry-run validation for invalid skill frontmatter before apply.
- Add conflict policy for importing over an existing DB custom skill.
- Decide whether public skills should remain bundled/read-only or get a separate DB seed/import path for fully remote stateless deployments.

---

## Batch 34: Sandbox Materializer Foundation

Date: 2026-06-19

### Goal

Add a provider-agnostic foundation for materializing DB-backed runtime context files into any sandbox that implements the shared `Sandbox` interface.

### Steps

1. Add failing materializer tests:
   - write text files under the materializer context root, now `/tmp/deerflow/context`.
   - write binary files through `Sandbox.update_file()`.
   - write a manifest file.
   - skip all writes when the manifest hash already matches.
2. Add `deerflow.sandbox.materializer`:
   - `SandboxMaterializedFile`.
   - `SandboxMaterializerManifest`.
   - `SandboxMaterializerResult`.
   - `SandboxMaterializer`.
3. Add path safety:
   - only relative manifest paths are accepted.
   - absolute paths and `..` traversal are rejected.
4. Add idempotence:
   - compute deterministic manifest hash from revision and file hashes.
   - compare existing `.manifest.json` before writing.
   - write `.manifest.json` last after all files are materialized.

### Files Changed

- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/tests/test_sandbox_materializer.py`

### Verification

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
```

Result:

```text
2 passed, 1 warning in 0.15s
```

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

### Remaining Work

- Build materializer manifests from DB memory, agent, and skill snapshots.
- Call the materializer after sandbox acquisition in eager and lazy sandbox paths.
- Add remote AIO integration tests that verify context files are written through the sandbox API rather than host bind mounts.

---

## Batch 35: Full Verification Compatibility Fix

Date: 2026-06-19

### Goal

Run broad verification after the DB/stateless implementation batches and fix regressions found outside the focused suites.

### Steps

1. Run full backend tests.
2. Identify failures:
   - `test_client_live.py` failed because local `config.yaml` has no configured models.
   - `test_invoke_acp_agent_tool.py` failed because `reload_extensions_config(None)` passed an explicit `None` into monkeypatched `ExtensionsConfig.from_file`.
3. Fix `reload_extensions_config()`:
   - call `ExtensionsConfig.from_file()` with no positional argument when `config_path is None`.
   - preserve explicit path behavior when a path is provided.
4. Re-run ACP focused suite.
5. Re-run non-live full backend suite.
6. Run full ruff and diff checks.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_config.py`

### Verification

```bash
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py -q
```

Result:

```text
17 passed, 1 warning in 0.40s
```

```bash
uv --directory backend run pytest -q --ignore=tests/test_client_live.py
```

Result:

```text
4738 passed, 15 skipped, 12 warnings in 81.33s
```

```bash
uv --directory backend run ruff check
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Full `uv --directory backend run pytest -q` still requires a local live model configuration for `tests/test_client_live.py`; current environment has no configured models, so those live tests fail before making a model call.
- Continue sandbox materializer integration: DB snapshot builder and middleware/provider invocation.

---

## Batch 36: Sandbox Runtime Context Snapshot Materialization

Date: 2026-06-19

### Goal

Complete the sandbox materializer integration by building runtime-context manifests from DB-backed memory, agent, and skill stores, then materializing them after lazy and eager sandbox acquisition.

### Steps

1. Add failing manifest-builder coverage:
   - seed DB user memory, agent-scoped memory, user profile, agent soul, custom skill, and skill support file.
   - build a sandbox manifest for a specific user, agent, and skill allowlist.
   - assert memory, profile, soul, `SKILL.md`, and support files are present in the manifest.
2. Implement `SandboxRuntimeContextManifestBuilder`:
   - emit `memory/user.json`.
   - emit `memory/agents/<agent>.json`.
   - emit `agent/USER.md` and `agent/SOUL.md`.
   - emit skill files under `skills/<category>/<skill>/...`.
3. Add failing lazy sandbox integration coverage:
   - sync `ensure_sandbox_initialized()`.
   - async `ensure_sandbox_initialized_async()`.
   - file-mode skip behavior.
4. Implement DB-mode lazy materialization:
   - gate by `DEER_FLOW_CONFIG_SOURCE=db`.
   - resolve `user_id` through `resolve_runtime_user_id(runtime)`.
   - resolve `agent_name` and skill allowlist from runtime context/config metadata.
   - record materialization hash, changed flag, file count, and sandbox id in runtime context.
5. Add failing eager middleware coverage:
   - sync `before_agent()`.
   - async `abefore_agent()`.
6. Implement eager materialization after sandbox acquisition when provider returns a sandbox instance.
7. Add stale-file pruning:
   - compare old `.manifest.json` files with the new manifest.
   - remove files no longer present in the new snapshot before writing the new manifest.

### Files Changed

- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/packages/harness/deerflow/sandbox/tools.py`
- `backend/packages/harness/deerflow/sandbox/middleware.py`
- `backend/tests/test_sandbox_materializer.py`
- `backend/tests/test_sandbox_middleware.py`

### Verification

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
```

Result:

```text
7 passed, 1 warning in 0.34s
```

```bash
uv --directory backend run pytest tests/test_sandbox_middleware.py -q
```

Result:

```text
20 passed, 1 warning in 0.35s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py tests/test_sandbox_file_operation_tools.py -q
```

Result:

```text
48 passed, 1 warning in 0.48s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py packages/harness/deerflow/sandbox/tools.py packages/harness/deerflow/sandbox/materializer.py packages/harness/deerflow/sandbox/middleware.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Add a real remote AIO smoke/integration test that verifies files are written through the sandbox API in a container-backed environment.
- Decide whether materialization failures in DB/stateless mode should fail closed instead of recording `sandbox_context_error` and continuing.

---

## Batch 37: Runtime State Import Preflight And Conflict Guard

Date: 2026-06-19

### Goal

Add preflight validation to the runtime-state import command so migration does not silently import malformed source files or overwrite existing DB rows.

### Steps

1. Add failing preflight tests:
   - dry-run reports invalid memory JSON without creating a sqlite DB file.
   - apply refuses to overwrite an existing user profile row.
2. Add source validation to inventory reports:
   - agent config YAML parse errors.
   - memory JSON parse/top-level errors and lightweight memory shape errors.
   - skill `SKILL.md` frontmatter validation and directory-name mismatch.
3. Add DB conflict preflight:
   - runtime app config and extensions config rows.
   - user profiles.
   - custom agents.
   - memory scopes.
   - custom skills.
4. Keep dry-run non-mutating:
   - skip sqlite DB conflict checks when the sqlite file does not exist.
   - do not create DB schema during dry-run preflight.
5. Fail fast on `--apply` if preflight has source errors or DB conflicts.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
8 passed, 1 warning in 0.31s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py tests/test_sandbox_file_operation_tools.py -q
```

Result:

```text
48 passed, 1 warning in 0.48s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py packages/harness/deerflow/sandbox/tools.py packages/harness/deerflow/sandbox/materializer.py packages/harness/deerflow/sandbox/middleware.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

```bash
uv --directory backend run pytest -q --ignore=tests/test_client_live.py
```

Result:

```text
4747 passed, 15 skipped, 12 warnings in 81.94s
```

### Remaining Work

- Add an explicit overwrite/import-replace mode if operators need to re-run a migration over existing DB rows.
- Add deeper `AgentConfig` semantic validation to preflight if migration should catch model/tool-group issues before apply.

---

## Batch 38: Runtime State Import Overwrite Mode And Agent Preflight Validation

Date: 2026-06-19

### Goal

Complete the migration command's documented overwrite path and move custom-agent semantic validation from apply-time failure into dry-run preflight.

### Steps

1. Add failing tests:
   - `overwrite=True` should allow `--apply` to replace an existing DB user profile conflict and report the overwritten conflict count.
   - dry-run preflight should catch invalid `AgentConfig` semantics before creating a sqlite DB file.
2. Add agent config preflight validation:
   - parse `config.yaml` as before.
   - fill the directory name as fallback `name`.
   - validate agent name.
   - run `AgentConfig.model_validate()` and report validation errors in `preflight.errors`.
3. Add overwrite mode:
   - add `overwrite` parameter to `import_runtime_state_to_db()`.
   - add CLI `--overwrite`.
   - keep `preflight.conflicts` visible.
   - treat conflicts as non-blocking only when overwrite is enabled.
   - report `applied.overwritten_conflicts` when overwrite mode is used.
4. Preserve safety:
   - source validation errors still block apply even with overwrite.
   - dry-run remains non-mutating for new sqlite DB targets.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_existing_db_conflicts -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_agent_config_semantics -q
```

Result:

```text
1 passed, 1 warning
1 passed, 1 warning
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
10 passed, 1 warning in 0.32s
```

### Remaining Work

- Add overwrite coverage for custom agents, memory scopes, custom skills, app config, and MCP rows if operators need per-resource replacement guarantees rather than the generic conflict count.
- Add a CLI-level smoke test for `scripts/import_runtime_state_to_db.py --apply --overwrite` once the script command is promoted to a stable operator workflow.

---

## Batch 39: Strict Sandbox Runtime Context Materialization

Date: 2026-06-19

### Goal

Add an explicit strict stateless-mode switch so sandbox initialization can fail closed when DB runtime-context materialization fails.

### Steps

1. Add a failing strict-mode test:
   - run DB-mode sandbox initialization with a failing runtime-context manifest builder.
   - set strict materialization behavior.
   - assert `SandboxRuntimeError` is raised.
   - assert `sandbox_context_error` is recorded and no manifest hash is recorded.
2. Add `SandboxConfig.runtime_context_fail_closed`:
   - default `False` for compatibility.
   - enable `True` for strict stateless deployments.
3. Update materialization error handling:
   - continue recording `sandbox_context_error`.
   - keep fail-open behavior by default.
   - raise `SandboxRuntimeError` when `runtime_context_fail_closed` is enabled.
4. Add AppConfig payload coverage for the new sandbox flag.

### Files Changed

- `backend/packages/harness/deerflow/config/sandbox_config.py`
- `backend/packages/harness/deerflow/sandbox/tools.py`
- `backend/tests/test_config_sources.py`
- `backend/tests/test_sandbox_materializer.py`

### Verification

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_ensure_sandbox_initialized_raises_when_strict_runtime_context_materialization_fails -q
```

Result:

```text
1 passed, 1 warning in 0.33s
```

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
```

Result:

```text
8 passed, 1 warning in 0.34s
```

```bash
uv --directory backend run pytest tests/test_config_sources.py::test_app_config_from_payload_loads_strict_sandbox_runtime_context_flag -q
```

Result:

```text
1 passed, 1 warning in 0.16s
```

```bash
uv --directory backend run pytest tests/test_config_sources.py tests/test_import_runtime_state_to_db.py tests/test_sandbox_materializer.py tests/test_sandbox_middleware.py tests/test_sandbox_file_operation_tools.py -q
```

Result:

```text
61 passed, 1 warning in 0.59s
```

```bash
uv --directory backend run ruff check
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

```bash
uv --directory backend run pytest -q --ignore=tests/test_client_live.py
```

Result:

```text
4751 passed, 15 skipped, 12 warnings in 81.81s
```

### Remaining Work

- Add operator documentation for when compatibility mode versus strict stateless mode should be used.

---

## Batch 40: Runtime State Import Overwrite Resource Accounting

Date: 2026-06-19

### Goal

Improve migration overwrite auditability by reporting overwrite conflicts grouped by resource type.

### Steps

1. Add a failing overwrite accounting test:
   - seed an existing DB user profile.
   - seed an existing DB user memory row.
   - provide replacement file-backed profile and memory sources.
   - run `import_runtime_state_to_db(..., apply=True, overwrite=True)`.
   - assert both resources are replaced.
   - assert `applied.overwritten_by_resource` reports `user_profile: 1` and `memory: 1`.
2. Implement `_count_conflicts_by_resource()`.
3. Extend overwrite apply summary:
   - keep `overwritten_conflicts` as the total.
   - add deterministic `overwritten_by_resource` counts.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_reports_conflicts_by_resource -q
```

Result:

```text
1 passed, 1 warning in 0.28s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
11 passed, 1 warning in 0.37s
```

### Remaining Work

- Add overwrite replacement tests for app config, extensions/MCP, custom agents, and custom skills.
- Consider adding per-resource changed/replaced counts from the apply functions themselves, not only from preflight conflicts.

---

## Batch 41: Runtime State Import Overwrite Agent And Skill Coverage

Date: 2026-06-19

### Goal

Strengthen overwrite-mode evidence for custom agents and custom skills so the migration command's replacement behavior is covered beyond user profiles and memory.

### Steps

1. Add coverage for custom-agent overwrite:
   - seed an existing DB custom agent.
   - provide a file-backed replacement `config.yaml` and `SOUL.md`.
   - run `import_runtime_state_to_db(..., apply=True, overwrite=True)`.
   - assert the DB agent config and soul are replaced.
2. Add coverage for custom-skill overwrite:
   - seed an existing DB custom skill.
   - provide a file-backed replacement custom skill.
   - run the same overwrite import.
   - assert the DB skill content is replaced.
3. Assert `applied.overwritten_by_resource` reports both `agent: 1` and `skill: 1`.

### Files Changed

- `backend/tests/test_import_runtime_state_to_db.py`

### Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_agents_and_custom_skills -q
```

Result:

```text
1 passed, 1 warning in 0.27s
```

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
12 passed, 1 warning in 0.37s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Add overwrite replacement tests for app config and extensions/MCP.
- Consider adding per-resource changed/replaced counts from apply functions themselves, not only preflight conflict counts.

---

## Batch 42: Runtime State Import Overwrite Config, Extensions, And MCP Coverage

Date: 2026-06-19

### Goal

Close the remaining overwrite-mode coverage gap for app config, extensions skill state, and first-class MCP server rows.

### Steps

1. Add a failing overwrite test for config/extensions/MCP:
   - import seed `config.yaml` and `extensions_config.json` into DB.
   - update the same file-backed sources with replacement values.
   - run `import_runtime_state_to_db(..., apply=True, overwrite=True)`.
   - assert `runtime_configs.app` is replaced.
   - assert `runtime_configs.extensions` skill state is replaced.
   - assert `mcp_servers.github` is replaced through `DbMcpServerStore`.
   - assert overwrite accounting reports `app_config`, `extensions_config`, and `mcp_server`.
2. Confirm the test fails because preflight only counted the aggregate app/extensions rows:
   - expected `overwritten_conflicts == 3`.
   - actual `overwritten_conflicts == 2`.
3. Extend `_collect_existing_db_conflicts()` to inspect the `mcp_servers` table:
   - when an imported MCP server name already exists, report `{"resource": "mcp_server", "name": ...}`.
   - keep the aggregate `extensions_config` conflict for the runtime extensions row.
4. Fix import ordering after lint feedback.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-overwrite-config-mcp-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp -q
```

Result:

```text
FAILED ... assert 2 == 3
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp -q
```

Result:

```text
1 passed, 1 warning in 0.27s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py -q
```

Result:

```text
22 passed, 1 warning in 0.51s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Consider apply-function changed/replaced counts if operators need to distinguish "conflict existed before apply" from "row content actually changed".
- App config import still writes the parsed YAML payload directly; full `AppConfig.from_payload()` dry-run validation remains a production-hardening item.

---

## Batch 43: Runtime State Import App Config Semantic Preflight

Date: 2026-06-19

### Goal

Move app config validation from parser-level only to `AppConfig.from_payload()` semantics during migration dry-run and apply preflight.

### Steps

1. Add a failing dry-run test:
   - write `config.yaml` with valid YAML but invalid app config semantics.
   - use `models: not-a-list` to trigger Pydantic validation.
   - run `import_runtime_state_to_db(..., apply=False)`.
   - assert `preflight.ok` is false.
   - assert the error is reported as `app_config`.
   - assert dry-run still does not create the sqlite DB file.
2. Confirm current behavior incorrectly reports the preflight as OK.
3. Add `_app_config_error()`:
   - parse YAML using the existing helper.
   - call `AppConfig.from_payload(..., apply_singletons=False)` so validation matches runtime config loading without mutating singleton state.
   - return validation exceptions as source errors.
4. Wire app config semantic errors into `_collect_source_errors()`.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-app-config-preflight-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database -q
```

Result:

```text
FAILED ... assert True is False
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database -q
```

Result:

```text
1 passed, 1 warning in 0.25s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_config_sources.py -q
```

Result:

```text
24 passed, 1 warning in 0.47s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Add full `ExtensionsConfig` semantic preflight for invalid MCP/skill extension shapes before apply.
- Consider reporting structured Pydantic error details instead of raw exception strings if operator UX needs machine-readable remediation.

---

## Batch 44: Runtime State Import Extensions Semantic Preflight

Date: 2026-06-19

### Goal

Move `extensions_config.json` / `mcp_config.json` validation from parser-level only to `ExtensionsConfig` semantics during migration dry-run and apply preflight.

### Steps

1. Add a failing dry-run test:
   - write valid JSON with invalid extensions semantics: `{"mcpServers": [], "skills": {}}`.
   - run `import_runtime_state_to_db(..., apply=False)`.
   - assert `preflight.ok` is false.
   - assert the error is reported as `extensions_config`.
   - assert dry-run still does not create the sqlite DB file.
2. Confirm current behavior incorrectly reports the preflight as OK.
3. Add `_extensions_config_error()`:
   - parse JSON using the existing helper.
   - call `ExtensionsConfig.model_validate(ExtensionsConfig.resolve_env_variables(payload))` so validation matches the file loading path.
   - return validation exceptions as source errors.
4. Wire extensions semantic errors into `_collect_source_errors()`.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-extensions-preflight-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database -q
```

Result:

```text
FAILED ... assert True is False
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database -q
```

Result:

```text
1 passed, 1 warning in 0.24s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_config_sources.py -q
```

Result:

```text
34 passed, 1 warning in 0.64s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Consider reporting structured Pydantic error details instead of raw exception strings if operator UX needs machine-readable remediation.
- Add CLI-level smoke coverage for `--apply --overwrite` once the migration command is promoted to a stable operator workflow.

---

## Batch 45: Runtime State Import CLI Bootstrap Database URL

Date: 2026-06-19

### Goal

Make the migration CLI use the same bootstrap DB URL mechanism as DB-mode startup, and add a real `--apply --overwrite` smoke test through the command-line entrypoint.

### Steps

1. Add a failing CLI smoke test:
   - seed an existing DB user profile through `DbAgentStore`.
   - write a file-backed replacement `USER.md`.
   - run `scripts/import_runtime_state_to_db.py --apply --overwrite` in a subprocess.
   - set `DEER_FLOW_DATABASE_URL=sqlite:///...` for the subprocess.
   - assert the CLI writes to the intended sqlite DB and replaces the profile.
2. Confirm current behavior fails because `main()` always constructs `DatabaseConfig()`:
   - default backend is `memory`.
   - apply preflight attempts DB conflict detection and raises `Runtime state import --apply requires sqlite or postgres database backend`.
3. Update `main()` to use `get_bootstrap_database_config() or DatabaseConfig()`.
4. Keep the Python API unchanged:
   - callers that pass `database_config` still fully control the target DB.
   - CLI now honors the same `DEER_FLOW_DATABASE_URL` used by DB-mode startup.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-cli-bootstrap-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_uses_bootstrap_database_url -q
```

Result:

```text
FAILED ... ValueError: Runtime state import --apply requires sqlite or postgres database backend
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_uses_bootstrap_database_url -q
```

Result:

```text
1 passed, 1 warning in 0.68s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py -q
```

Result:

```text
25 passed, 1 warning in 1.04s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Add documented operator examples for setting `DEER_FLOW_DATABASE_URL` before running the migration command.
- Consider explicit `--database-url` CLI option if operators need one-off migration targets without exporting environment variables.

---

## Batch 46: Runtime State Import Explicit Database URL CLI Option

Date: 2026-06-19

### Goal

Add an explicit `--database-url` option to the migration CLI so operators can target a one-off migration DB without exporting `DEER_FLOW_DATABASE_URL`.

### Steps

1. Add a failing CLI smoke test:
   - seed an existing DB user profile through `DbAgentStore`.
   - write a file-backed replacement `USER.md`.
   - run `scripts/import_runtime_state_to_db.py --database-url sqlite:///... --apply --overwrite` in a subprocess.
   - remove `DEER_FLOW_DATABASE_URL` from the subprocess environment.
   - assert the CLI writes to the intended sqlite DB and replaces the profile.
2. Confirm current behavior fails at argparse:
   - `--database-url` is an unrecognized argument.
3. Refactor bootstrap URL parsing:
   - add `database_config_from_url(url)` to `deerflow.config.bootstrap`.
   - make `get_bootstrap_database_config()` call the shared parser.
4. Add CLI `--database-url`:
   - explicit argument overrides `DEER_FLOW_DATABASE_URL`.
   - env var remains the default bootstrap path.
   - absent explicit/env URL keeps the legacy `DatabaseConfig()` fallback.

### Operator Examples

Environment-based target:

```bash
DEER_FLOW_DATABASE_URL=sqlite:////absolute/path/to/deerflow.db \
  uv --directory backend run python scripts/import_runtime_state_to_db.py \
  --project-root /path/to/project \
  --state-dir /path/to/.deer-flow \
  --apply --overwrite
```

One-off explicit target:

```bash
uv --directory backend run python scripts/import_runtime_state_to_db.py \
  --project-root /path/to/project \
  --state-dir /path/to/.deer-flow \
  --database-url sqlite:////absolute/path/to/deerflow.db \
  --apply --overwrite
```

### Files Changed

- `backend/packages/harness/deerflow/config/bootstrap.py`
- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-database-url-cli-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_accepts_database_url_argument -q
```

Result:

```text
FAILED ... error: unrecognized arguments: --database-url sqlite:///...
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_accepts_database_url_argument -q
```

Result:

```text
1 passed, 1 warning in 0.68s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py -q
```

Result:

```text
26 passed, 1 warning in 1.58s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py packages/harness/deerflow/config/bootstrap.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Consider adding CLI tests for invalid `--database-url` diagnostics if the migration command becomes a public operator interface.
- Reduce import-time warning noise on CLI stderr so scripted use is cleaner.

---

## Batch 47: Runtime State Import CLI Database URL Diagnostics

Date: 2026-06-19

### Goal

Make invalid migration CLI database URLs fail with argparse-style diagnostics instead of a Python traceback.

### Steps

1. Add a failing CLI diagnostic test:
   - run `scripts/import_runtime_state_to_db.py --database-url not-a-db-url --apply` in a subprocess.
   - remove `DEER_FLOW_DATABASE_URL` from the subprocess environment.
   - assert exit code `2`.
   - assert stderr contains the supported sqlite/postgresql URL message.
   - assert stderr does not contain `Traceback`.
2. Confirm current behavior fails with exit code `1` and a raw `ValueError` traceback.
3. Update `main()`:
   - keep DB URL parsing behavior unchanged.
   - catch `ValueError` around CLI DB config resolution.
   - pass the message to `parser.error(str(exc))`.
4. Keep the Python API unchanged:
   - invalid `DatabaseConfig` or explicitly passed API configuration still behaves through the existing function paths.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-cli-diagnostics-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_reports_invalid_database_url_without_traceback -q
```

Result:

```text
FAILED ... assert 1 == 2
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_reports_invalid_database_url_without_traceback -q
```

Result:

```text
1 passed, 1 warning in 0.63s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py -q
```

Result:

```text
27 passed, 1 warning in 2.02s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py packages/harness/deerflow/config/bootstrap.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Reduce import-time warning noise on CLI stderr so scripted use is cleaner.
- Consider moving more operator-facing validation failures into structured JSON if this command gets a UI wrapper.

---

## Batch 48: Runtime State Import CLI Help Import Hygiene

Date: 2026-06-19

### Goal

Keep the migration CLI help/argument parsing path lightweight so `--help` does not load runtime config and emit unrelated warnings on stderr.

### Steps

1. Add a failing subprocess test:
   - run `scripts/import_runtime_state_to_db.py --help`.
   - assert exit code `0`.
   - assert stdout contains the CLI description.
   - assert stderr is empty.
2. Confirm current behavior fails because top-level imports load runtime/config modules:
   - `LangChainPendingDeprecationWarning` appears.
   - `config.yaml` version/model warnings appear.
3. Move DeerFlow runtime imports out of module top-level and into the functions that need them:
   - App config and extensions validation imports are local to preflight helpers.
   - DB store/model imports are local to DB conflict/apply helpers.
   - skill validation/storage imports are local to skill scan/apply helpers.
   - CLI bootstrap imports are local to `main()`.
4. Add a `TYPE_CHECKING` import for `DatabaseConfig` so annotations stay readable without eager runtime imports.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-cli-help-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_help_does_not_load_runtime_config_warnings -q
```

Result:

```text
FAILED ... assert result.stderr == ""
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_help_does_not_load_runtime_config_warnings -q
```

Result:

```text
1 passed, 1 warning in 0.30s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py -q
```

Result:

```text
28 passed, 1 warning in 1.57s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py packages/harness/deerflow/config/bootstrap.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Pytest still observes a third-party LangGraph pending-deprecation warning when tests import deeper runtime paths; this is separate from the CLI subprocess stderr contract.
- Consider splitting the migration script into a thin CLI module and an implementation module if import hygiene becomes harder to maintain.

---

## Batch 49: MCP Legacy Aggregate Payload Migration

Date: 2026-06-19

### Goal

Close the remaining MCP compatibility gap where old DB deployments can still have MCP server definitions embedded under `runtime_configs.extensions.payload_json.mcpServers` instead of first-class `mcp_servers` rows.

### Steps

1. Add a failing regression test:
   - seed `runtime_configs.extensions` with legacy `mcpServers`, `skills`, and `mcpInterceptors`.
   - leave the `mcp_servers` table empty.
   - load through `DbExtensionsConfigStore`.
   - assert the facade still returns the same `ExtensionsConfig` view.
   - assert the MCP server is materialized into `mcp_servers`.
   - assert the aggregate runtime payload no longer contains `mcpServers`.
2. Confirm the test fails for the expected reason:
   - the facade reads the legacy aggregate MCP payload.
   - `DbMcpServerStore` still has no `github` row.
3. Extract MCP row writes into `DbMcpServerStore.save_mcp_servers_in_session()`:
   - keep existing normalization, stale revision/hash checks, delete-missing semantics, and row revision behavior.
   - let callers reuse the same write logic inside an existing SQLAlchemy transaction.
4. Update `DbExtensionsConfigStore.load_extensions_config()`:
   - when `mcp_servers` is empty and the legacy aggregate has a non-empty mapping, save those servers to `mcp_servers`.
   - remove `mcpServers` from `runtime_configs.extensions`.
   - increment the aggregate row revision and refresh its content hash.
   - preserve `updated_by` from the legacy row.
5. Keep invalid legacy MCP payload behavior unchanged:
   - non-dict `mcpServers` values are not migrated.
   - schema validation still fails through `ExtensionsConfig` validation.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/packages/harness/deerflow/config/mcp_store.py`
- `backend/tests/test_extensions_config_sources.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-mcp-legacy-aggregate-migration-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py::test_db_extensions_config_store_migrates_legacy_aggregate_mcp_servers -q
```

Result:

```text
FAILED ... KeyError: 'github'
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py::test_db_extensions_config_store_migrates_legacy_aggregate_mcp_servers -q
```

Result:

```text
1 passed, 1 warning in 0.17s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
29 passed, 1 warning in 1.63s
```

```bash
uv --directory backend run ruff check packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/mcp_store.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py
```

Result:

```text
All checks passed.
```

### Remaining Work

- Direct writes through `DbMcpServerStore` still do not bump `runtime_configs.extensions.revision`; keep that store internal until a dedicated aggregate revision/meta row is introduced.
- This batch migrates legacy aggregate MCP payloads opportunistically on DB extensions load. If operators need preflight visibility before any write, add an explicit dry-run command that reports the same migration before applying it.

---

## Batch 50: MCP Direct Store Writes Bump Extensions Revision

Date: 2026-06-19

### Goal

Close the MCP cache-invalidation gap where direct writes through `DbMcpServerStore.save_mcp_servers()` could update first-class MCP rows without changing the aggregate `runtime_configs.extensions.revision` observed by DB-mode extensions and MCP tool caches.

### Steps

1. Add a failing cache regression test:
   - seed DB extensions through `DbExtensionsConfigStore` with MCP server `github`.
   - reset and populate the DB-mode `get_extensions_config()` singleton.
   - write MCP server `linear` directly through `DbMcpServerStore.save_mcp_servers()`.
   - assert the next `get_extensions_config()` call refreshes to `linear`.
2. Confirm current behavior fails because the cached singleton still returns `github`.
3. Add `DbMcpServerStore.bump_extensions_runtime_revision_in_session()`:
   - create `runtime_configs.extensions` with `{"skills": {}}` when direct MCP writes happen before an aggregate row exists.
   - remove any legacy aggregate `mcpServers` field.
   - increment revision and refresh content hash for existing aggregate rows.
4. Update direct `DbMcpServerStore.save_mcp_servers()`:
   - save MCP rows.
   - bump the aggregate extensions runtime row in the same transaction.
5. Update `DbExtensionsConfigStore.save_extensions_config()`:
   - use `DbMcpServerStore.save_mcp_servers_in_session()` in the same transaction as the aggregate row update.
   - preserve the existing facade revision semantics and stale `expected_revision` checks.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/packages/harness/deerflow/config/mcp_store.py`
- `backend/tests/test_extensions_config_sources.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-mcp-direct-write-revision-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py::test_db_extensions_config_cache_refreshes_after_direct_mcp_store_write -q
```

Result:

```text
FAILED ... assert 'linear' in {'github': McpServerConfig(...)}
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py::test_db_extensions_config_cache_refreshes_after_direct_mcp_store_write -q
```

Result:

```text
1 passed, 1 warning in 0.18s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_cache_revision.py tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
31 passed, 1 warning in 1.74s
```

```bash
uv --directory backend run ruff check packages/harness/deerflow/config/extensions_sources.py packages/harness/deerflow/config/mcp_store.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_cache_revision.py
```

Result:

```text
All checks passed.
```

### Remaining Work

- The aggregate revision is still a compatibility bridge. If MCP gets its own public API with partial row updates, consider a dedicated metadata/revision table for MCP instead of relying on `runtime_configs.extensions`.
- Direct MCP writes now invalidate caches, but they still replace the full MCP server set. Add partial upsert/delete APIs only with explicit conflict and revision semantics.

---

## Batch 51: Runtime State Import Skill File Size Preflight

Date: 2026-06-19

### Goal

Prevent migration from silently importing oversized custom skill support files into the DB-backed skill store, matching the technical design requirement that V1 reports or rejects assets that are too large for pragmatic DB storage.

### Steps

1. Add a failing dry-run/preflight test:
   - create a custom skill with `SKILL.md`.
   - add `assets/large.bin` with 5 bytes.
   - set `DEER_FLOW_DB_SKILL_FILE_MAX_BYTES=4`.
   - assert dry-run reports one risk.
   - assert the skill report includes total support bytes and the oversized file risk.
   - assert preflight blocks apply with a `skill_file` error.
   - assert dry-run still does not create the DB file.
2. Confirm the test fails because the inventory previously only counted support files and reported zero risks.
3. Add skill-file threshold handling:
   - default threshold is 5 MiB.
   - `DEER_FLOW_DB_SKILL_FILE_MAX_BYTES` overrides the threshold.
   - invalid env values fall back to the default.
4. Extend skill inventory:
   - compute `support_file_bytes`.
   - attach `skill-file-too-large` risks per oversized support file.
   - include skill risks in the summary risk count.
5. Extend preflight:
   - convert oversized support-file risks into blocking `skill_file` errors.
   - keep normal skill frontmatter/schema errors unchanged.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-skill-size-preflight-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files -q
```

Result:

```text
FAILED ... assert 0 == 1
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files -q
```

Result:

```text
1 passed, 1 warning in 0.16s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
20 passed, 1 warning in 1.51s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
All checks passed.
```

### Remaining Work

- The default limit is a conservative V1 import guard, not a long-term asset-storage design. Large skill assets still need object storage or a separate file table if they become a supported workload.
- The current check is per support file. Add a total skill/package size limit if DB growth from many small files becomes a migration risk.

---

## Batch 52: Runtime State Import Public Skill Reporting

Date: 2026-06-19

### Goal

Make the migration report explicit about the V1 public-skill policy: custom skills are imported into DB, while public skills remain file-backed/read-only and are skipped by apply.

### Steps

1. Add a failing report assertion to the custom skill apply regression:
   - seed one custom skill and one public skill.
   - apply migration.
   - assert `applied.skills == 1`.
   - assert `applied.skipped_public_skills == 1`.
   - assert skill inventory includes `import_action` for both skill categories.
2. Confirm the test fails because apply output only reported imported custom skills.
3. Extend skill inventory:
   - custom skills report `import_action: import-to-db`.
   - public skills report `import_action: file-backed-skip`.
4. Extend skill apply summary:
   - count skipped public skills separately.
   - keep custom skill import count unchanged.
5. Update exact apply-output expectations for runs with no public skills:
   - report `skipped_public_skills: 0`.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-public-skill-reporting-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_custom_skills -q
```

Result:

```text
FAILED ... KeyError: 'skipped_public_skills'
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_custom_skills -q
```

Result:

```text
1 passed, 1 warning in 0.26s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
20 passed, 1 warning in 1.54s
```

```bash
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
All checks passed.
```

### Remaining Work

- Public skills remain a deployment artifact in V1. Fully remote stateless deployments still need a product decision: bundle immutable public skills with the image, seed them into DB, or serve them from an external artifact store.
- If public skills become DB-seeded later, introduce a separate schema/API rather than overloading the current custom-skill rows.

---

## Batch 53: DB-backed Default Agent SOUL Runtime Path

Date: 2026-06-19

### Goal

Remove the remaining DB-mode runtime dependency on the local default-agent `SOUL.md` file by storing default-agent SOUL content in DB and materializing it into sandbox runtime context for the default agent.

### Steps

1. Add failing tests:
   - `DbAgentStore` can save/load per-user default-agent SOUL.
   - `load_agent_soul(None, user_id=...)` reads DB in DB config mode instead of local `SOUL.md`.
   - `setup_agent` with `agent_name=None` writes default-agent SOUL to DB in DB mode and does not write the global file.
   - `SandboxRuntimeContextManifestBuilder` includes `agent/SOUL.md` for the default agent when DB default SOUL exists.
2. Confirm all tests fail because the store has no default SOUL API and DB mode still falls back to files for `agent_name=None`.
3. Add `DefaultAgentSoulRow`:
   - table: `default_agent_souls`.
   - primary key: `owner_user_id`.
   - payload: `soul_text`, `revision`, timestamps.
4. Extend `DbAgentStore`:
   - `load_default_agent_soul(owner_user_id)`.
   - `save_default_agent_soul(owner_user_id, soul_text)`.
5. Extend `agents_config`:
   - DB mode `load_agent_soul(None, user_id=...)` reads default-agent SOUL from DB.
   - add `save_default_agent_soul(...)` helper with file-mode fallback.
6. Update `setup_agent`:
   - DB mode default-agent creation saves SOUL through the new helper.
   - custom-agent DB behavior remains unchanged.
7. Update sandbox materialization:
   - custom agent keeps loading `load_agent_soul(user_id, agent_name)`.
   - default agent loads `load_default_agent_soul(user_id)`.

### Files Changed

- `backend/packages/harness/deerflow/persistence/agents/model.py`
- `backend/packages/harness/deerflow/persistence/models/__init__.py`
- `backend/packages/harness/deerflow/config/agent_store.py`
- `backend/packages/harness/deerflow/config/agents_config.py`
- `backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py`
- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/tests/test_db_agent_store.py`
- `backend/tests/test_setup_agent_tool.py`
- `backend/tests/test_sandbox_materializer.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-default-agent-soul-db-runtime-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_db_agent_store.py::test_db_agent_store_default_agent_soul tests/test_db_agent_store.py::test_agents_config_helpers_read_default_agent_soul_from_db_in_db_mode tests/test_setup_agent_tool.py::TestSetupAgentNoDataLoss::test_db_mode_default_agent_soul_is_written_to_db_not_global_file tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_reads_default_agent_soul -q
```

Result:

```text
FAILED ... AttributeError: 'DbAgentStore' object has no attribute 'load_default_agent_soul'
FAILED ... AttributeError: 'DbAgentStore' object has no attribute 'save_default_agent_soul'
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_db_agent_store.py::test_db_agent_store_default_agent_soul tests/test_db_agent_store.py::test_agents_config_helpers_read_default_agent_soul_from_db_in_db_mode tests/test_setup_agent_tool.py::TestSetupAgentNoDataLoss::test_db_mode_default_agent_soul_is_written_to_db_not_global_file tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_reads_default_agent_soul -q
```

Result:

```text
4 passed, 1 warning in 0.28s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py tests/test_custom_agent.py tests/test_update_agent_tool.py -q
```

Result:

```text
114 passed, 2 warnings in 0.95s
```

### Remaining Work

- Default-agent SOUL is now DB-backed at runtime, but existing legacy `SOUL.md` files still need migration into `default_agent_souls`.
- The current DB row is per user. Legacy global `SOUL.md` migration maps to owner `default` for compatibility.

---

## Batch 54: Runtime State Import Default Agent SOUL

Date: 2026-06-19

### Goal

Import legacy top-level `SOUL.md` into DB-backed default-agent SOUL storage so migrations do not leave the default agent dependent on local files after DB mode is enabled.

### Steps

1. Add failing migration tests:
   - inventory reports `state_dir/SOUL.md` as `default_agent_souls`.
   - summary includes `default_agent_souls`.
   - apply stores the content in `DbAgentStore.load_default_agent_soul("default")`.
   - apply summary includes `default_agent_souls`.
2. Confirm tests fail because the migration report has no `default_agent_souls` section.
3. Add `_default_agent_soul_reports()`:
   - detects top-level `state_dir/SOUL.md`.
   - maps it to owner `default`.
   - marks it as legacy.
4. Add default-agent SOUL conflict detection:
   - if `default_agent_souls.owner_user_id` already exists, report a `default_agent_soul` conflict.
5. Extend apply:
   - read each default SOUL file.
   - save through `DbAgentStore.save_default_agent_soul(...)`.
   - report `default_agent_souls` count.
6. Keep custom-agent import unchanged:
   - `agents` still counts only custom/legacy named agents.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-default-agent-soul-review.md`

### Verification

Red test before implementation:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_agents_and_user_profiles tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_default_agent_soul -q
```

Result:

```text
FAILED ... KeyError: 'default_agent_souls'
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_agents_and_user_profiles tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_default_agent_soul -q
```

Result:

```text
3 passed, 1 warning in 0.28s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py tests/test_import_runtime_state_to_db.py -q
```

Result:

```text
54 passed, 1 warning in 1.78s
```

```bash
uv --directory backend run ruff check packages/harness/deerflow/persistence/agents/model.py packages/harness/deerflow/persistence/models/__init__.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py packages/harness/deerflow/tools/builtins/setup_agent_tool.py packages/harness/deerflow/sandbox/materializer.py scripts/import_runtime_state_to_db.py tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
All checks passed.
```

### Remaining Work

- Add overwrite-specific regression coverage for `default_agent_soul` conflicts if operators need per-resource replacement assertions beyond generic conflict accounting.
- Decide whether future UI/API surfaces should expose default-agent SOUL separately from custom-agent management.

---

## Batch 55: Runtime State Import Default Agent SOUL Overwrite Coverage

Date: 2026-06-19

### Goal

Close the migration coverage gap for replacing an existing DB-backed default-agent SOUL row with `--apply --overwrite`.

### Steps

1. Review the existing overwrite path:
   - preflight gathers existing DB conflicts.
   - `overwrite=True` makes conflicts non-blocking.
   - apply writes default-agent SOUL through `DbAgentStore.save_default_agent_soul(...)`.
2. Add a focused regression test:
   - seed `default_agent_souls.default` with existing content.
   - create legacy `state_dir/SOUL.md` with replacement content.
   - run `import_runtime_state_to_db(..., apply=True, overwrite=True)`.
   - assert `preflight.conflicts` reports `default_agent_soul`.
   - assert `overwritten_by_resource` includes `default_agent_soul: 1`.
   - assert content is replaced and row revision advances to 2.
3. Run the new targeted test.
4. Update the default-agent SOUL import review to mark overwrite-specific coverage as closed.
5. Add a dedicated review document for this overwrite coverage batch.

### Files Changed

- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-default-agent-soul-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-default-agent-soul-overwrite-review.md`

### Verification

Coverage test before production changes:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_default_agent_soul -q
```

Result:

```text
1 passed, 1 warning in 0.28s
```

This test passed against the existing production code, so no production code change was made in this batch. The behavior was already provided by Batch 54's conflict detection plus `DbAgentStore.save_default_agent_soul(...)` upsert semantics.

### Remaining Work

- Decide whether future UI/API surfaces should expose default-agent SOUL separately from custom-agent management.
- Add operator runbook notes for the legacy `default` owner mapping before production migration.

---

## Batch 56: Default Agent SOUL API Surface

Date: 2026-06-20

### Goal

Expose default-agent SOUL as a first-class management API so DB/stateless mode does not require local `SOUL.md` writes or custom-agent route overloads to edit the default agent identity.

### Steps

1. Add failing API tests:
   - file mode `GET /api/default-agent-soul` returns `content: null` when no `SOUL.md` exists.
   - file mode `PUT /api/default-agent-soul` writes top-level `SOUL.md`.
   - file mode read-after-write returns the saved content.
   - DB mode `PUT /api/default-agent-soul` writes `default_agent_souls` and does not create local `SOUL.md`.
   - disabled management API returns 403 for both GET and PUT.
2. Confirm the new tests fail with 404 because the route does not exist.
3. Add default-agent SOUL request/response models.
4. Add `GET /api/default-agent-soul`:
   - enforce `agents_api.enabled`.
   - resolve the effective user.
   - read through `load_agent_soul(None, user_id=...)`.
5. Add `PUT /api/default-agent-soul`:
   - enforce `agents_api.enabled`.
   - resolve the effective user.
   - write through `save_default_agent_soul(...)`.
6. Keep persistence mode selection inside the existing helper:
   - file mode writes top-level `SOUL.md`.
   - DB mode writes `default_agent_souls`.

### Files Changed

- `backend/app/gateway/routers/agents.py`
- `backend/tests/test_custom_agent.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-default-agent-soul-db-runtime-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-default-agent-soul-api-review.md`

### Verification

Red tests before implementation:

```bash
uv --directory backend run pytest tests/test_custom_agent.py::TestDefaultAgentSoulAPI tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_uses_db_not_soul_md_file tests/test_custom_agent.py::TestAgentsApiDisabled::test_default_agent_soul_routes_return_403 -q
```

Result:

```text
FAILED ... assert 404 == 200
FAILED ... assert 404 == 403
```

Green targeted tests:

```bash
uv --directory backend run pytest tests/test_custom_agent.py::TestDefaultAgentSoulAPI tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_uses_db_not_soul_md_file tests/test_custom_agent.py::TestAgentsApiDisabled::test_default_agent_soul_routes_return_403 -q
```

Result:

```text
5 passed, 2 warnings in 0.49s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_custom_agent.py tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py -q
```

Result:

```text
99 passed, 2 warnings in 1.02s
```

```bash
uv --directory backend run ruff check app/gateway/routers/agents.py tests/test_custom_agent.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Add operator runbook notes for the legacy `default` owner mapping before production migration.
- Consider whether the API should expose default-agent SOUL revision metadata if concurrent admin editing becomes a supported workflow.

---

## Batch 57: Operator Runbook For Stateless DB Migration

Date: 2026-06-20

### Goal

Close the operator documentation gap for production migration, especially the legacy global-file mapping to the compatibility owner `default`.

### Steps

1. Review the remaining Batch 56 item:
   - operator runbook notes are needed for the legacy `default` owner mapping before production migration.
2. Add a dedicated runbook:
   - scope of V1 import.
   - legacy source to DB target mapping.
   - explicit warning that owner `default` is a compatibility owner, not a real user.
   - dry-run command examples.
   - apply command examples.
   - overwrite command examples and interpretation.
   - DB-mode startup env.
   - post-migration checks.
   - rollback guidance.
   - known V1 boundaries.
3. Keep the runbook operational and link it back to the plan, technical design, and implementation log.
4. Add a review document for the runbook batch.

### Files Changed

- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-operator-runbook-review.md`

### Verification

```bash
git diff --check
```

Result:

```text
git diff --check produced no output.
```

### Remaining Work

- Consider whether the API should expose default-agent SOUL revision metadata if concurrent admin editing becomes a supported workflow.

---

## Batch 58: Runbook Cross-References For Strict Context And Default Owner

Date: 2026-06-20

### Goal

Clean up stale review status after adding the operator runbook, and document when operators should use compatibility versus strict sandbox runtime-context materialization.

### Steps

1. Review older findings that still read as open:
   - default-agent SOUL legacy owner mapping.
   - strict sandbox runtime-context operator guidance.
   - fail-open materialization policy from before strict mode existed.
2. Extend the operator runbook:
   - add compatibility versus strict runtime-context modes.
   - document `sandbox.runtime_context_fail_closed`.
   - explain rollout usage versus strict stateless usage.
   - note that changing the flag requires restart.
3. Update older review documents:
   - mark default-agent SOUL owner mapping as documented by Batch 57.
   - mark strict runtime-context operator guidance as documented by Batch 58.
   - mark fail-closed behavior as implemented by Batch 39 and documented by Batch 58.
4. Add a dedicated review document for this cleanup batch.

### Files Changed

- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-runtime-state-import-default-agent-soul-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-default-agent-soul-db-runtime-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-strict-sandbox-runtime-context-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-sandbox-runtime-context-materialization-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-runbook-cross-reference-review.md`

### Verification

```bash
git diff --check
```

Result:

```text
git diff --check produced no output.
```

### Remaining Work

- Consider whether the API should expose default-agent SOUL revision metadata if concurrent admin editing becomes a supported workflow.

---

## Batch 59: Default Agent SOUL Revision And Conditional Writes

Date: 2026-06-20

### Goal

Add revision metadata and optimistic conditional writes to the default-agent SOUL API so concurrent admin edits can be detected instead of silently overwriting each other.

### Steps

1. Add failing API tests:
   - empty GET returns `content: null` and `revision: 0`.
   - file-mode PUT returns a positive revision.
   - file-mode PUT with mismatched `expected_revision` returns 409 and preserves `SOUL.md`.
   - DB-mode PUT returns revision 1 for a new row.
   - DB-mode conditional PUT with current revision advances to revision 2.
   - DB-mode stale conditional PUT returns 409 and preserves the current DB value.
2. Confirm tests fail because the response has no `revision` and PUT ignores `expected_revision`.
3. Add DB store support:
   - `load_default_agent_soul_state(owner_user_id)` returns content and revision.
   - `save_default_agent_soul(..., expected_revision=...)` rejects stale revisions.
4. Add active-store helpers:
   - `DefaultAgentSoulState`.
   - `DefaultAgentSoulConflictError`.
   - `load_default_agent_soul_state(...)`.
   - `save_default_agent_soul_state(...)`.
5. Update the gateway route:
   - response model includes `revision`.
   - update request accepts `expected_revision`.
   - stale writes return 409 with reload-and-retry guidance.
6. Update operator docs and reviews:
   - runbook documents the revision workflow.
   - previous review item about concurrent admin edits is marked fixed.

### Files Changed

- `backend/packages/harness/deerflow/config/agent_store.py`
- `backend/packages/harness/deerflow/config/agents_config.py`
- `backend/app/gateway/routers/agents.py`
- `backend/tests/test_custom_agent.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-default-agent-soul-api-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-operator-runbook-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-default-agent-soul-revision-review.md`

### Verification

Red tests before implementation:

```bash
uv --directory backend run pytest tests/test_custom_agent.py::TestDefaultAgentSoulAPI tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_uses_db_not_soul_md_file tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_db_update_rejects_stale_revision -q
```

Result:

```text
FAILED ... KeyError: 'revision'
FAILED ... assert 200 == 409
```

Green targeted tests:

```bash
uv --directory backend run pytest tests/test_custom_agent.py::TestDefaultAgentSoulAPI tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_uses_db_not_soul_md_file tests/test_custom_agent.py::TestAgentsAPIDbMode::test_default_agent_soul_db_update_rejects_stale_revision -q
```

Result:

```text
6 passed, 2 warnings in 0.52s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_custom_agent.py tests/test_db_agent_store.py tests/test_setup_agent_tool.py tests/test_sandbox_materializer.py -q
```

Result:

```text
101 passed, 2 warnings in 1.06s
```

```bash
uv --directory backend run ruff check app/gateway/routers/agents.py packages/harness/deerflow/config/agent_store.py packages/harness/deerflow/config/agents_config.py tests/test_custom_agent.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Continue requirement audit against the stateless DB plan before marking the overall goal complete.

---

## Batch 61: Requirement Audit Against Stateless DB Plan

Date: 2026-06-20

### Goal

Create a requirement-level audit that compares the current implementation against the stateless DB plan and technical design before any overall completion claim.

### Steps

1. Re-read the implementation plan and technical design:
   - in-scope runtime state categories.
   - subsystem tasks 1 through 8.
   - rollout phases.
   - final acceptance criteria.
2. Inspect current implementation evidence:
   - config/bootstrap/runtime config stores.
   - extensions/MCP stores and cache revision paths.
   - agent/profile/default-agent SOUL stores and APIs.
   - memory DB storage and updater behavior.
   - custom skill DB storage, router/tool integration, and prompt cache behavior.
   - sandbox materializer and runtime-context middleware wiring.
   - import/runtime-state migration tool.
3. Inspect focused test evidence by subsystem.
4. Compare the implementation to each acceptance criterion and label status as verified, partial, evidence gap, design divergence, or deferred V1 boundary.
5. Identify the next highest-priority implementation gap:
   - skills progressive loading remains incomplete against the design.
6. Add a review document for the audit itself.

### Files Changed

- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requirement-audit-review.md`

### Verification

```bash
git diff --check
```

Result:

```text
git diff --check produced no output.
```

### Remaining Work

- Implement or explicitly de-scope the skills progressive-loading requirement.
- Add a full DB-mode stateless thread/run smoke with legacy runtime files absent or read-only.
- Add real remote/container AIO materialization proof or a stronger production contract test.
- Define the final full-suite/live-test verification strategy before marking the goal complete.

---

## Batch 62: Skill Progressive Loading Storage API

Date: 2026-06-20

### Goal

Close the eager support-file materialization part of the skills progressive-loading gap and add storage APIs for on-demand skill file access.

### Steps

1. Add failing DB skill storage tests:
   - `load_skills()` should materialize only `SKILL.md`, not support files.
   - DB storage should list a file manifest and read a support file without materializing the support tree.
2. Confirm red behavior:
   - `references/notes.md` was materialized during listing.
   - `DbSkillStorage` had no `list_skill_file_manifest()` API.
3. Add `SkillFileManifest`.
4. Add base `SkillStorage.read_skill_file()` and `SkillStorage.list_skill_file_manifest()` filesystem implementations.
5. Add DB-backed custom-skill overrides that read `SKILL.md` and support-file payloads directly from DB rows.
6. Change DB listing to materialize only `SKILL.md`.
7. Export `SkillFileManifest` from `deerflow.skills.storage`.
8. Update the requirement audit and add a review document for this batch.

### Files Changed

- `backend/packages/harness/deerflow/skills/storage/skill_storage.py`
- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/packages/harness/deerflow/skills/storage/__init__.py`
- `backend/tests/test_db_skill_storage.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-skill-progressive-loading-storage-review.md`

### Verification

Red tests:

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py::test_db_skill_storage_load_skills_materializes_only_skill_md tests/test_db_skill_storage.py::test_db_skill_storage_reads_file_and_manifest_without_materializing_tree -q
```

Result:

```text
FAILED ... assert not ... references/notes.md.exists()
FAILED ... AttributeError: 'DbSkillStorage' object has no attribute 'list_skill_file_manifest'
```

Green targeted tests:

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py::test_db_skill_storage_load_skills_materializes_only_skill_md tests/test_db_skill_storage.py::test_db_skill_storage_reads_file_and_manifest_without_materializing_tree -q
```

Result:

```text
2 passed, 1 warning in 0.17s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skills_custom_router.py tests/test_skill_manage_tool.py tests/test_sandbox_materializer.py tests/test_lead_agent_prompt.py -q
```

Result:

```text
54 passed, 2 warnings in 1.04s
```

```bash
uv --directory backend run ruff check packages/harness/deerflow/skills/storage/skill_storage.py packages/harness/deerflow/skills/storage/db_skill_storage.py packages/harness/deerflow/skills/storage/__init__.py tests/test_db_skill_storage.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Implement true metadata-first skill listing or explicitly de-scope it for V1.
- Add a full DB-mode stateless thread/run smoke with legacy runtime files absent or read-only.
- Add real remote/container AIO materialization proof or a stronger production contract test.

---

## Batch 63: DB Custom Skill Metadata-First Listing

Date: 2026-06-20

### Goal

Close the remaining V1 skills progressive-loading gap by making DB-backed custom skill listing use persisted metadata instead of reparsing materialized `SKILL.md` content.

### Steps

1. Add a failing DB skill storage test proving `load_skills()` must not call `parse_skill_file()` for DB custom skills:
   - write a custom skill with description, license, and `allowed-tools`.
   - corrupt the materialized cache copy of `SKILL.md`.
   - monkeypatch `deerflow.skills.parser.parse_skill_file` to raise.
   - assert `load_skills()` still returns metadata from DB and refreshes the cache from DB.
2. Confirm the red behavior:
   - existing `DbSkillStorage` inherited the base `load_skills()`, which parsed materialized `SKILL.md`.
3. Add `custom_skills.metadata_json` to persist parsed metadata.
4. Parse and store custom skill metadata when DB storage writes or installs `SKILL.md`.
5. Override `DbSkillStorage.load_skills()`:
   - keep public skills filesystem-parsed.
   - build DB custom `Skill` objects from `metadata_json`.
   - materialize only `SKILL.md` so prompt-exposed paths remain valid.
   - preserve extensions enabled-state merging and sorting.
6. Add compatibility for existing `custom_skills` tables missing `metadata_json`, with fallback metadata extraction/backfill.
7. Add an old-schema regression test that creates the pre-Batch-63 table shape and verifies startup/listing still works.
8. Update the requirement audit and add a review document for this batch.

### Files Changed

- `backend/packages/harness/deerflow/persistence/skills/model.py`
- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/tests/test_db_skill_storage.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-db-skill-metadata-first-listing-review.md`

### Verification

Red test:

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py::test_db_skill_storage_load_skills_uses_db_metadata_without_reparsing_skill_md -q
```

Result:

```text
FAILED ... AssertionError: DB custom skill listing must not parse materialized SKILL.md
```

Green targeted tests:

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py::test_db_skill_storage_load_skills_uses_db_metadata_without_reparsing_skill_md -q
uv --directory backend run pytest tests/test_db_skill_storage.py::test_db_skill_storage_backfills_metadata_column_for_existing_table -q
```

Result:

```text
1 passed, 1 warning in 0.16s
1 passed, 1 warning in 0.17s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skills_custom_router.py tests/test_skill_manage_tool.py tests/test_sandbox_materializer.py tests/test_lead_agent_prompt.py -q
```

Result:

```text
56 passed, 2 warnings in 1.08s
```

```bash
uv --directory backend run ruff check packages/harness/deerflow/skills/storage/db_skill_storage.py packages/harness/deerflow/persistence/skills/model.py tests/test_db_skill_storage.py
```

Result:

```text
All checks passed.
```

### Remaining Work

- Add a full DB-mode stateless thread/run smoke with legacy runtime files absent or read-only.
- Add real remote/container AIO materialization proof or a stronger production contract test.
- Decide whether normalized `skills` / `skill_files` tables and public-skill DB storage are later-phase requirements.

---

## Batch 64: DB-Mode Gateway Run Lifecycle Smoke

Date: 2026-06-20

### Goal

Close the DB-mode startup/thread/run evidence gap by proving the Gateway can start from DB bootstrap configuration and execute a normal streaming run while local runtime config files are absent.

### Steps

1. Reuse the existing runtime lifecycle E2E harness instead of creating a synthetic run path:
   - FastAPI `TestClient` with real app lifespan.
   - real auth/register flow.
   - real `/api/threads` and `/api/threads/{thread_id}/runs/stream` endpoints.
   - real `start_run()`, `run_agent()`, StreamBridge, checkpointer, run store, and thread metadata store.
   - fake deterministic agent factory to avoid external LLM calls.
2. Add DB-mode runtime config seeding into `runtime_configs`:
   - `app` payload contains model, local sandbox, disabled title/memory, sqlite-backed app database, and memory run-events.
   - `extensions` payload contains empty MCP/skills config.
3. Add an `isolated_db_app` fixture that sets:
   - `DEER_FLOW_CONFIG_SOURCE=db`
   - `DEER_FLOW_DATABASE_URL=sqlite+aiosqlite:///.../deerflow.db`
   - `DEER_FLOW_CONFIG_PATH` and `DEER_FLOW_EXTENSIONS_CONFIG_PATH` to absent files.
4. Add `test_db_mode_stream_run_completes_without_local_runtime_config_files`.
5. Confirm the initial failure was fixture-local:
   - SQLite could not open the DB because the parent directory did not exist.
6. Fix the fixture by creating the bootstrap DB parent directory before seeding.
7. Update the requirement audit and add a review document for this batch.

### Files Changed

- `backend/tests/test_runtime_lifecycle_e2e.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-db-mode-run-lifecycle-smoke-review.md`

### Verification

Initial failure:

```bash
uv --directory backend run pytest tests/test_runtime_lifecycle_e2e.py::test_db_mode_stream_run_completes_without_local_runtime_config_files -q
```

Result:

```text
ERROR ... sqlite3.OperationalError: unable to open database file
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_runtime_lifecycle_e2e.py::test_db_mode_stream_run_completes_without_local_runtime_config_files -q
```

Result:

```text
1 passed, 2 warnings in 0.78s
```

### Remaining Work

- Add real remote/container AIO materialization proof or a stronger production contract test.
- Keep normalized skill schema and public-skill DB storage as explicit later-phase choices.
- Run final broader verification before marking the overall stateless DB plan complete.

---

## Batch 65: Sandbox Context Skill File Storage API Materialization

Date: 2026-06-20

### Goal

Ensure DB-backed custom skill support files and binary assets are included in sandbox runtime-context materialization even when the host skill cache is absent or only contains `SKILL.md`.

### Steps

1. Tighten the existing sandbox runtime-context manifest test:
   - write a DB custom skill.
   - write `references/notes.md` and `assets/logo.bin` into DB storage.
   - remove `skills-cache/custom` before building the sandbox manifest.
   - assert `SKILL.md`, text support file, and binary asset all appear in the manifest.
2. Confirm the red behavior:
   - `SandboxRuntimeContextManifestBuilder` used `skill.skill_dir.rglob("*")`.
   - after Batch 63, DB listing only rematerializes `SKILL.md`, so DB support files were omitted.
3. Change `SandboxRuntimeContextManifestBuilder._iter_skill_files()` to use:
   - `skill_storage.list_skill_file_manifest(skill.name, skill.category)`
   - `skill_storage.read_skill_file(skill.name, skill.category, item.relative_path)`
4. Add an `AioSandbox` contract test proving runtime-context materialization writes:
   - text files through `file.write_file(file=..., content=...)`.
   - binary files through `file.write_file(file=..., content=<base64>, encoding="base64")`.
5. Update the requirement audit and add a review document for this batch.

### Files Changed

- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/tests/test_sandbox_materializer.py`
- `backend/tests/test_aio_sandbox.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-sandbox-context-skill-storage-api-review.md`

### Verification

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

### Remaining Work

- Add a live Docker/K8s/provisioner AIO smoke if production acceptance requires real remote sandbox proof.
- Keep host-path translation and DooD deployment notes in the operator runbook.
- Run final broader verification before marking the overall stateless DB plan complete.

---

## Batch 66: Requirement Audit Remaining-Work Refresh

Date: 2026-06-20

### Goal

Keep the requirement audit usable as the controlling checklist after Batches 63-65 closed previously listed gaps.

### Steps

1. Re-read the current requirement audit against the latest implementation log.
2. Identify stale bottom-section blockers:
   - skills metadata-first listing.
   - DB-mode end-to-end runtime smoke.
3. Confirm those items are now covered by Batches 63 and 64.
4. Replace the stale remaining-work list with current blockers:
   - live AIO/container materialization proof.
   - public-skill DB storage / normalized skill schema scope decision.
   - final verification strategy.
5. Update the completion decision to say Batches 63-65 closed the V1 metadata-first, DB-mode gateway smoke, and DB support-file sandbox materialization gaps.
6. Add a review document for this audit refresh.

### Files Changed

- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requirement-audit-refresh-review.md`

### Verification

Document-only batch. Final whitespace verification is run with the next code/doc verification batch.

### Remaining Work

- Determine whether a live AIO/container smoke can run in the current environment or should remain an operator-runbook gate.
- Keep public-skill/normalized-schema scope explicit.
- Run final verification before declaring the active goal complete.

---

## Batch 67: Live AIO Prompt Skill Path Materialization

Date: 2026-06-20

### Goal

Close the local Docker AIO materialization proof gap without confusing `/tmp/deerflow/context` snapshot files with prompt-exposed skill paths.

### Steps

1. Upgrade the live Docker AIO smoke to assert DB skill files are written to `/mnt/skills/custom/live/...`, not only to `/tmp/deerflow/context/skills/...`.
2. Confirm the red behavior:
   - `SandboxMaterializer(skills_root="/mnt/skills")` was not supported.
   - after adding skill-root routing, the live AIO smoke failed with `Permission denied: '/mnt/skills'`.
3. Investigate the container path:
   - the AIO image has root-owned `/mnt`.
   - the AIO shell/file API user cannot create `/mnt/skills`.
   - `/tmp/deerflow/context` remains writable, but it is not the path exposed in skill prompts.
4. Add skill-root routing to `SandboxMaterializer`:
   - manifest entries under `skills/...` are written to `skills_root`.
   - memory/agent/context entries remain under `/tmp/deerflow/context`.
   - the manifest remains at `/tmp/deerflow/context/.manifest.json`.
5. Pass `config.skills.container_path` into runtime DB materialization.
6. Add a DB-mode AIO local-container mount path:
   - per-thread host scratch directory `{thread}/skills`.
   - chmod `0o777`, matching existing sandbox user-data mount behavior.
   - mounted writable at `skills.container_path` in DB mode.
   - skip the old read-only skill-source mount in DB mode.
7. Update docs and audit status:
   - local Docker AIO is now verified.
   - remote provisioner/K8s must provide an equivalent writable `skills.container_path` volume or configure a writable path.

### Files Changed

- `backend/packages/harness/deerflow/config/paths.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/packages/harness/deerflow/sandbox/tools.py`
- `backend/tests/test_aio_sandbox_live.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `backend/tests/test_sandbox_materializer.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-plan.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-aio-prompt-skill-path-review.md`

### Verification

Red materializer test:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_sandbox_materializer_writes_skill_files_to_prompt_container_path -q
```

Result:

```text
FAILED ... TypeError: SandboxMaterializer.__init__() got an unexpected keyword argument 'skills_root'
```

Red live Docker AIO smoke after adding skill-root routing:

```bash
DEER_FLOW_RUN_LIVE_AIO_SANDBOX=1 uv --directory backend run pytest tests/test_aio_sandbox_live.py -q --tb=short
```

Result:

```text
FAILED ... OSError: [Errno 13] Permission denied: '/mnt/skills'
```

Green targeted tests:

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

Result:

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

### Remaining Work

- Keep remote provisioner/K8s writable `skills.container_path` as an explicit production rollout gate unless a remote live smoke is added.
- Keep public-skill/normalized-schema scope explicit.
- Run final focused and broader verification before declaring the active goal complete.

---

## Batch 68: V1 Skill Scope Decision Record

Date: 2026-06-20

### Goal

Resolve the remaining public-skill and normalized-schema product ambiguity by documenting the V1 boundary instead of leaving it as an open requirement.

### Steps

1. Review current `DbSkillStorage` behavior:
   - custom skills are DB-backed mutable state.
   - public skills remain filesystem-backed/read-only.
   - `metadata_json`, `list_skill_file_manifest()`, and `read_skill_file()` close the V1 progressive-loading behavior for custom skills.
2. Review migration/operator docs:
   - public skills are already reported as `file-backed-skip`.
   - custom skills are imported/applied to DB.
3. Add a dedicated V1 scope decision document:
   - public skills are immutable deployment artifacts in V1.
   - normalized `skills` / `skill_files` schema is deferred.
   - remote provisioner/K8s writable `skills.container_path` remains a rollout gate.
4. Update the requirement audit:
   - public-skill/normalized-schema is no longer an undecided blocker.
   - it remains a visible V1 boundary and later-phase migration path.

### Files Changed

- `docs/harness-stateless-db-mode-v1-scope-decisions.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-v1-scope-decisions-review.md`

### Verification

Document-only batch. Whitespace verification is run with the next verification batch.

### Remaining Work

- Keep remote provisioner/K8s writable `skills.container_path` as an explicit production rollout gate unless a remote live smoke is added.
- Run final focused and broader verification before declaring the active goal complete.

---

## Batch 69: Final Backend Verification And Live Client Gate

Date: 2026-06-20

### Goal

Convert the final verification strategy from an open item into concrete evidence and an explicit environment gate for live client tests.

### Steps

1. Run unfiltered backend pytest to see whether the latest worktree can pass the full suite.
2. Confirm the only failures are in `tests/test_client_live.py`.
3. Inspect the failure root cause:
   - local `config.yaml` has no configured models.
   - `create_chat_model()` fails on `config.models[0]`.
   - `list_models()` returns an empty list.
4. Run full backend pytest excluding the live-client suite.
5. Run full backend ruff and whitespace checks.
6. Update the requirement audit and add a review document.

### Files Changed

- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-final-verification-review.md`

### Verification

Unfiltered full backend suite:

```bash
uv --directory backend run pytest -q
```

Result:

```text
10 failed, 4794 passed, 17 skipped, 12 warnings in 84.67s
```

All failures were in `tests/test_client_live.py` and were caused by the local model config being empty:

```text
IndexError: list index out of range
No models are configured in /Users/idefav/Documents/sources/deer-flow/config.yaml.
```

Full backend suite excluding live-client environment tests:

```bash
uv --directory backend run pytest -q --ignore=tests/test_client_live.py
```

Result:

```text
4786 passed, 16 skipped, 12 warnings in 83.85s
```

Static and whitespace checks:

```bash
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Keep remote provisioner/K8s writable `skills.container_path` as an explicit production rollout gate unless a remote live smoke is added.
- Run `tests/test_client_live.py` only in an environment with real model config.

---

## Batch 70: Provisioner Extra Mount Contract Forwarding

Date: 2026-06-20

### Goal

Close the gateway-side gap where DB-mode writable skills mount requirements were computed by `AioSandboxProvider` but silently dropped before reaching `RemoteSandboxBackend` / provisioner mode.

### Steps

1. Inspect `RemoteSandboxBackend.create()` and `_provisioner_create()`.
2. Confirm `extra_mounts` was accepted by the method signature but ignored in the `POST /api/sandboxes` JSON payload.
3. Add a failing test proving provisioner mode should receive `extra_mounts` as JSON objects:
   - `host_path`
   - `container_path`
   - `read_only`
4. Implement payload serialization in `_provisioner_create()`.
5. Keep the existing no-mount payload shape unchanged when `extra_mounts` is absent.
6. Update the technical design, operator runbook, requirement audit, and review document.

### Files Changed

- `backend/packages/harness/deerflow/community/aio_sandbox/remote_backend.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-provisioner-extra-mount-contract-review.md`

### Verification

Red test:

```bash
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_remote_backend_create_forwards_extra_mounts -q
```

Result:

```text
FAILED ... KeyError: 'extra_mounts'
```

Green targeted tests:

```bash
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_remote_backend_create_forwards_extra_mounts -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_remote_backend_create_forwards_effective_user_id -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py tests/test_sandbox_materializer.py tests/test_aio_sandbox.py tests/test_aio_sandbox_live.py -q
uv --directory backend run ruff check packages/harness/deerflow/community/aio_sandbox/remote_backend.py tests/test_aio_sandbox_provider.py
git diff --check
```

Result:

```text
1 passed, 1 warning in 0.17s
1 passed, 1 warning in 0.17s
68 passed, 1 skipped, 1 warning in 0.93s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- The external provisioner/K8s service must consume the `extra_mounts` payload and create the writable `skills.container_path` volume or configure a writable skills path.
- Run a remote provisioner smoke when that environment is available.
- Run `tests/test_client_live.py` only in an environment with real model config.

---

## Batch 71: Live Client Auto-skip For Unconfigured Model Environments

Date: 2026-06-20

### Goal

Make the full backend test suite usable in environments without configured live models while preserving `tests/test_client_live.py` as an active live gate when model config exists.

### Steps

1. Use the previous full-suite failure as red evidence:
   - `tests/test_client_live.py` failed because local `config.yaml` existed but had an empty `models` list.
   - the module only skipped in CI or when `config.yaml` was missing.
2. Add a module-level live test gate:
   - load `get_app_config()`.
   - if `models` is empty, mark the module skipped.
   - if config loading fails, mark the module skipped with the config-load error.
3. Replace module-level `pytest.skip()` with `pytestmark = pytest.mark.skip(...)` so explicitly running `tests/test_client_live.py` exits cleanly with skipped tests instead of pytest exit code 5.
4. Re-run `tests/test_client_live.py`.
5. Re-run full backend pytest without excluding live client.
6. Update the requirement audit and review document.

### Files Changed

- `backend/tests/test_client_live.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-client-skip-gate-review.md`

### Verification

Targeted live-client gate:

```bash
uv --directory backend run pytest tests/test_client_live.py -q
```

Intermediate result with module-level `pytest.skip()`:

```text
1 skipped, 1 warning in 0.26s
exit code 5
```

Green result after switching to `pytestmark`:

```text
19 skipped, 1 warning in 0.30s
```

Full backend suite:

```bash
uv --directory backend run pytest -q
```

Result:

```text
4787 passed, 35 skipped, 12 warnings in 84.44s
```

### Remaining Work

- The external provisioner/K8s service must consume the `extra_mounts` payload and create the writable `skills.container_path` volume or configure a writable skills path.
- Run a remote provisioner smoke when that environment is available.
- Run `tests/test_client_live.py` as active live tests in an environment with real model config.

---

## Batch 72: Bundled Provisioner Extra Mount Consumption

Date: 2026-06-20

### Goal

Close the repo-owned provisioner-side gap for DB/stateless writable skill paths: the gateway sent `extra_mounts`, but `docker/provisioner/app.py` did not yet parse or render them into K8s Pod volumes/mounts.

### Steps

1. Inspect `docker/provisioner/app.py` and existing provisioner tests.
2. Add failing tests:
   - `ExtraMount` model should exist.
   - `_build_volumes(..., extra_mounts=...)` should replace the default skills volume when an extra mount targets `/mnt/skills`.
   - `_build_volume_mounts(..., extra_mounts=...)` should replace the default read-only `/mnt/skills` mount with the writable request-scoped mount.
   - `_build_pod(..., extra_mounts=...)` should wire the requested volumes and mounts.
   - `create_sandbox()` should pass parsed request `extra_mounts` to `_build_pod()`.
3. Implement `ExtraMount` in the provisioner request model.
4. Add request-scoped extra mount rendering:
   - stable volume names: `extra-mount-{index}`.
   - K8s `hostPath` volumes with `DirectoryOrCreate`.
   - request-scoped mounts override default mount paths.
5. Pass `req.extra_mounts` from `create_sandbox()` into `_build_pod()`.
6. Add `CreateSandboxRequest.model_rebuild(...)` so Pydantic resolves the nested model in dynamic test imports.
7. Update design/runbook/audit docs and add a review document.

### Files Changed

- `docker/provisioner/app.py`
- `backend/tests/test_provisioner_pvc_volumes.py`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-provisioner-extra-mount-consumption-review.md`

### Verification

Red tests:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::TestBuildVolumes::test_extra_mount_replaces_default_skills_volume tests/test_provisioner_pvc_volumes.py::TestBuildVolumeMounts::test_extra_mount_replaces_default_skills_mount tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_wires_extra_mounts -q
```

Result:

```text
FAILED ... AttributeError: module 'provisioner_app_test' has no attribute 'ExtraMount'
```

Endpoint red/green detail:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::test_create_sandbox_passes_extra_mounts_to_pod_builder -q
```

Intermediate result:

```text
FAILED ... PydanticUserError: `CreateSandboxRequest` is not fully defined
```

Green targeted and provisioner contract tests:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::TestBuildVolumes::test_extra_mount_replaces_default_skills_volume tests/test_provisioner_pvc_volumes.py::TestBuildVolumeMounts::test_extra_mount_replaces_default_skills_mount tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_wires_extra_mounts -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::test_create_sandbox_passes_extra_mounts_to_pod_builder -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py tests/test_provisioner_kubeconfig.py tests/test_remote_sandbox_backend.py tests/test_aio_sandbox_provider.py -q
uv --directory backend run ruff check ../docker/provisioner/app.py tests/test_provisioner_pvc_volumes.py
```

Result:

```text
3 passed, 1 warning in 0.31s
1 passed, 1 warning in 0.31s
79 passed, 1 warning in 0.61s
All checks passed!
```

### Remaining Work

- Run a real remote provisioner/K8s smoke to prove the deployed cluster/node path or PVC permissions allow the AIO shell/file API user to write to `skills.container_path`.
- Run `tests/test_client_live.py` as active live tests in an environment with real model config.

## Batch 60: DB Skill Toggle Prompt Cache Freshness (Late-Added Historical Note)

Date: 2026-06-20

Note: this historical Batch 60 section was appended after Batch 72 while closing out the audit trail. Later batches continue below it.

### Goal

Close the remaining skill prompt cache coverage gap by proving that a DB-backed skill disabled through `runtime_configs.extensions.skills` is no longer advertised from a stale in-process prompt cache.

### Steps

1. Add a real DB-backed regression test:
   - seed `DbExtensionsConfigStore` with `db-skill` enabled.
   - seed `DbSkillStorage` with a custom `SKILL.md`.
   - warm the enabled-skill prompt cache.
   - disable `db-skill` through `DbExtensionsConfigStore`.
   - call `get_skills_prompt_section()` without an explicit cache reset.
2. Confirm the initial isolated test first exposed missing MCP table bootstrap in `DbExtensionsConfigStore`.
3. Fix extensions-store metadata bootstrap by importing the ORM registration module before `Base.metadata.create_all()`.
4. Confirm the test then failed for the target stale-cache behavior:
   - `db-skill` remained in the prompt after the DB revision changed.
5. Update `get_cached_enabled_skills()` to run the revision-aware cache check before returning cached skills.
6. Update the older skill prompt cache review and add a dedicated review for this batch.

### Files Changed

- `backend/packages/harness/deerflow/config/extensions_sources.py`
- `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`
- `backend/tests/test_lead_agent_prompt.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-19-skill-prompt-cache-revision-invalidation-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-skill-prompt-db-toggle-cache-review.md`

### Verification

Red test after schema bootstrap was fixed:

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py::test_skills_prompt_section_drops_stale_db_enabled_skill_after_revision_change -q
```

Result:

```text
FAILED ... assert 'db-skill' not in '<skill_system>...'
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py::test_skills_prompt_section_drops_stale_db_enabled_skill_after_revision_change -q
```

Result:

```text
1 passed, 1 warning in 0.31s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py tests/test_extensions_config_sources.py tests/test_skills_custom_router.py tests/test_db_skill_storage.py -q
```

Result:

```text
46 passed, 2 warnings in 1.00s
```

```bash
uv --directory backend run ruff check packages/harness/deerflow/agents/lead_agent/prompt.py packages/harness/deerflow/config/extensions_sources.py tests/test_lead_agent_prompt.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

### Remaining Work

- Continue requirement audit against the stateless DB plan before marking the overall goal complete.

---

## Batch 73: MCP Lazy Reload Smoke

Date: 2026-06-20

### Goal

Close the higher-level MCP evidence gap by proving the real `get_cached_mcp_tools()` entrypoint reloads tools when DB-backed extensions config revision changes, not just the lower-level `_is_cache_stale()` helper.

### Steps

1. Inspect MCP cache, MCP tool loading, DB extensions store, and existing MCP revision tests.
2. Add a high-level DB-mode smoke test:
   - seed DB extensions config with one MCP server.
   - monkeypatch MCP tool discovery to return a revision-tagged sentinel tool.
   - call `get_cached_mcp_tools()` to warm the cache.
   - update DB extensions config to a new MCP server/revision.
   - call `get_cached_mcp_tools()` again and assert the returned tool is rebuilt for the new revision.
3. Run the new test and confirm the existing implementation already reloads correctly.
4. Fix the synchronous lazy-initialization path to use `asyncio.get_running_loop()` so no-current-loop contexts avoid the Python 3.12 `asyncio.get_event_loop()` deprecation warning.
5. Add this review and audit evidence.

### Files Changed

- `backend/packages/harness/deerflow/mcp/cache.py`
- `backend/tests/test_mcp_cache_revision.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-mcp-lazy-reload-smoke-review.md`

### Verification

Targeted MCP cache test:

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py -q
```

Initial result before the lazy-init warning cleanup:

```text
2 passed, 2 warnings in 0.43s
```

Green result after the lazy-init warning cleanup:

```text
2 passed, 1 warning in 0.42s
```

### Remaining Work

- Run a real remote provisioner/K8s smoke to prove the deployed cluster/node path or PVC permissions allow the AIO shell/file API user to write to `skills.container_path`.
- Run `tests/test_client_live.py` as active live tests in an environment with real model config.

---

## Batch 74: Remote Provisioner AIO Live Smoke Entrypoint

Date: 2026-06-20

### Goal

Turn the remaining remote provisioner/K8s rollout gate into an executable opt-in smoke test that operators can run against a real deployment.

### Steps

1. Inspect the existing local Docker AIO live smoke and `RemoteSandboxBackend`.
2. Add `tests/test_aio_sandbox_remote_live.py`:
   - default skip unless `DEER_FLOW_RUN_REMOTE_AIO_SANDBOX=1`.
   - require `DEER_FLOW_REMOTE_AIO_PROVISIONER_URL`.
   - require either `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH` or `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX`.
   - create a sandbox through `RemoteSandboxBackend`.
   - send writable `skills.container_path` through `extra_mounts`.
   - materialize agent context plus text/binary skill files through the AIO sandbox file API.
   - read the prompt-exposed skills path and binary file back from inside the sandbox.
   - destroy the sandbox in `finally`.
3. Add runbook instructions and environment variables for running the remote smoke.
4. Update the requirement audit to distinguish “remote smoke entrypoint exists” from “remote smoke has been executed in production-like K8s”.
5. Add this review document.

### Files Changed

- `backend/tests/test_aio_sandbox_remote_live.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-remote-provisioner-live-smoke-entrypoint-review.md`

### Verification

Default no-cluster behavior:

```bash
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
```

Result:

```text
1 skipped, 1 warning in 0.16s
```

### Remaining Work

- Execute the remote smoke in a real provisioner/K8s environment:

```bash
DEER_FLOW_RUN_REMOTE_AIO_SANDBOX=1 \
DEER_FLOW_REMOTE_AIO_PROVISIONER_URL=http://provisioner:8002 \
DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX=/var/lib/deerflow/runtime-skills \
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
```

- Run `tests/test_client_live.py` as active live tests in an environment with real model config.

---

## Batch 75: Pytest Live Gate Markers

Date: 2026-06-20

### Goal

Make the remaining external gates easier to collect and run by adding explicit pytest markers for live tests, remote live tests, and LLM-backed tests.

### Steps

1. Inspect existing pytest marker configuration and live test files.
2. Red-check live gate collection:
   - run `pytest --collect-only -m live` against the known live gate files.
   - confirm no tests are collected because no live markers exist yet.
3. Add marker declarations in `backend/pyproject.toml`:
   - `live`
   - `remote_live`
   - `requires_llm`
4. Mark live test modules:
   - `tests/test_client_live.py`: `live`, `requires_llm`, preserving module skip behavior when no model config exists.
   - `tests/test_aio_sandbox_live.py`: `live`.
   - `tests/test_aio_sandbox_remote_live.py`: `live`, `remote_live`.
   - `tests/test_create_deerflow_agent_live.py`: `live`, `requires_llm`.
5. Update runbook and requirement audit with marker-based commands.
6. Add this review document.

### Files Changed

- `backend/pyproject.toml`
- `backend/tests/test_client_live.py`
- `backend/tests/test_aio_sandbox_live.py`
- `backend/tests/test_aio_sandbox_remote_live.py`
- `backend/tests/test_create_deerflow_agent_live.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-pytest-live-gate-markers-review.md`

### Verification

Red collection check before markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py -q
```

Result:

```text
no tests collected (21 deselected) in 0.28s
exit code 5
```

Green collection check after markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
```

Result:

```text
24 tests collected in 0.34s
```

Default no-credentials/no-cluster execution:

```bash
uv --directory backend run pytest -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
```

Result:

```text
24 skipped, 1 warning in 0.34s
```

Remote-only gate selection:

```bash
uv --directory backend run pytest --collect-only -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
uv --directory backend run pytest -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
```

Result:

```text
1/24 tests collected (23 deselected) in 0.32s
1 skipped, 23 deselected, 1 warning in 0.32s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4792 passed, 36 skipped, 12 warnings in 83.99s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` in an environment with real model config and any required live service credentials.

---

## Batch 76: Complete Real-LLM Live Gate Marker Coverage

Date: 2026-06-20

### Goal

Close the marker coverage gap left after Batch 75 by adding real-LLM E2E gates that were still invisible to `pytest -m live`.

### Steps

1. Audit live-like test modules for explicit external credentials or opt-in flags.
2. Red-check the missed files:
   - `tests/test_client_e2e.py`
   - `tests/test_deferred_tool_promotion_real_llm.py`
3. Confirm `pytest --collect-only -m live` selected zero tests from those files.
4. Add marker coverage:
   - mark `test_deferred_tool_promotion_real_llm.py` as `live` and `requires_llm`, preserving `ONEAPI_E2E=1` opt-in skip behavior.
   - upgrade the existing `@requires_llm` decorator in `test_client_e2e.py` so only real-LLM test functions receive `live` and `requires_llm` markers.
   - keep non-LLM client E2E tests out of the `requires_llm` selection.
5. Update the runbook, requirement audit, implementation log, and review document.

### Files Changed

- `backend/tests/test_client_e2e.py`
- `backend/tests/test_deferred_tool_promotion_real_llm.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-real-llm-live-gate-marker-coverage-review.md`

### Verification

Red collection check before markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
no tests collected (44 deselected) in 0.25s
exit code 5
```

Focused green collection and default skip checks:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest -m live tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
12/44 tests collected (32 deselected) in 0.30s
12/44 tests collected (32 deselected) in 0.30s
12 skipped, 32 deselected, 1 warning in 0.29s
```

Full marked-gate collection:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest --collect-only -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
36/68 tests collected (32 deselected) in 0.36s
34/68 tests collected (34 deselected) in 0.36s
1/68 tests collected (67 deselected) in 0.36s
```

Default no-credentials/no-cluster execution:

```bash
uv --directory backend run pytest -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest -m requires_llm tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
36 skipped, 32 deselected, 1 warning in 0.38s
34 skipped, 34 deselected, 1 warning in 0.38s
1 skipped, 67 deselected, 1 warning in 0.37s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4792 passed, 36 skipped, 12 warnings in 83.91s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

## Batch 77: Docker Live Gate Marker Coverage

Date: 2026-06-20

### Goal

Add local Docker-backed E2E tests to the marked live-gate surface so `pytest -m live` also captures non-LLM external dependencies.

### Steps

1. Audit E2E tests that require a local Docker daemon.
2. Red-check `tests/test_sandbox_orphan_reconciliation_e2e.py`:
   - `pytest --collect-only -m live` selected zero tests.
3. Add `docker_live` marker registration in `backend/pyproject.toml`.
4. Mark `tests/test_sandbox_orphan_reconciliation_e2e.py` as `live` and `docker_live`.
5. Preserve the existing `Docker not available` skip behavior.
6. Update runbook, requirement audit, implementation log, and review document.

### Files Changed

- `backend/pyproject.toml`
- `backend/tests/test_sandbox_orphan_reconciliation_e2e.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-docker-live-gate-marker-review.md`

### Verification

Red collection check before markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_sandbox_orphan_reconciliation_e2e.py -q
```

Result:

```text
no tests collected (3 deselected) in 0.09s
exit code 5
```

Green collection and execution:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest --collect-only -m docker_live tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest -m docker_live tests/test_sandbox_orphan_reconciliation_e2e.py -q
```

Result:

```text
3 tests collected in 0.10s
3 tests collected in 0.09s
3 passed, 1 warning in 9.83s
```

Marked live-gate aggregate after adding `docker_live`:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest --collect-only -m docker_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py tests/test_sandbox_orphan_reconciliation_e2e.py -q
uv --directory backend run pytest -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py tests/test_sandbox_orphan_reconciliation_e2e.py -q
```

Result:

```text
39/71 tests collected (32 deselected) in 0.47s
3/71 tests collected (68 deselected) in 0.44s
3 passed, 36 skipped, 32 deselected, 1 warning in 10.03s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4792 passed, 36 skipped, 12 warnings in 84.05s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 78: Live Gate Marker Regression Guard

Date: 2026-06-20

### Goal

Make the live-gate marker contract self-checking so future real-LLM, remote sandbox, or Docker-backed tests cannot silently bypass `pytest -m live` / specialized marker selections.

### Steps

1. Re-audit the known external-dependency test signals:
   - real LLM opt-in and credential skip gates.
   - remote AIO sandbox opt-in gates.
   - local Docker-backed E2E gates.
2. Confirm fake-LLM HTTP/runtime E2E tests should remain in the normal suite and should not be forced under `live`.
3. Add a red meta-test with a missing-marker fixture for `DEER_FLOW_RUN_REMOTE_AIO_SANDBOX`.
4. Implement the marker detector using Python AST marker extraction instead of brittle text-only marker matching.
5. Scan every `backend/tests/test_*.py` file and require the expected markers when live opt-in signals are present.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/tests/test_live_gate_markers.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-gate-marker-regression-guard-review.md`

### Verification

Red detector check:

```bash
uv --directory backend run pytest tests/test_live_gate_markers.py -q
```

Result:

```text
1 failed, 1 passed, 1 warning in 0.19s
```

The failure was the expected missing-marker fixture:

```text
assert [] == ['test_remote_live_missing.py requires live, remote_live markers']
```

Green detector check:

```bash
uv --directory backend run pytest tests/test_live_gate_markers.py -q
```

Result:

```text
2 passed, 1 warning in 0.18s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4794 passed, 36 skipped, 12 warnings in 84.01s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 79: Startup-only Config Restart Metadata

Date: 2026-06-20

### Goal

Close the local proof gap for startup-only config changes by surfacing restart-required metadata from the DB runtime config write path, migration report, and an admin-readable Gateway endpoint.

### Steps

1. Re-check the plan and technical design references:
   - `config.yaml` startup-only fields should preserve reload-boundary behavior.
   - DB mode should expose changed startup-only fields as restart metadata for admin APIs and migration reports.
2. Add red tests for `RuntimeConfigRepository.upsert()`:
   - changed `database` / `sandbox` fields return restart-required metadata and reasons.
   - changed non-startup fields do not require restart.
3. Implement shared top-level changed-field detection and restart-required reason mapping from `config.reload_boundary.STARTUP_ONLY_FIELDS`.
4. Add a red migration apply report assertion for overwriting app config with changed `log_level` / `sandbox`.
5. Return `applied.restart_required.app_config` from the migration report when an existing DB app config row is overwritten with changed startup-only fields.
6. Add a red router test for `GET /api/config/reload-boundary`:
   - non-admin users are rejected.
   - admin users receive the startup-only prefix, fields, and reasons.
7. Implement and mount the `config` router, and add the OpenAPI tag.
8. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/app/gateway/app.py`
- `backend/app/gateway/routers/config.py`
- `backend/packages/harness/deerflow/persistence/runtime_config/__init__.py`
- `backend/packages/harness/deerflow/persistence/runtime_config/sql.py`
- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_config_router.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `backend/tests/test_runtime_config_store.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-startup-only-restart-metadata-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp -q
uv --directory backend run pytest tests/test_config_router.py -q
```

Results:

```text
Runtime config store: 2 failed, 9 passed, 1 warning in 0.23s
Import apply report: KeyError: 'restart_required'
Config router: ImportError: cannot import name 'config'
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_runtime_config_store.py -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_config_extensions_and_mcp -q
uv --directory backend run pytest tests/test_config_router.py -q
```

Results:

```text
11 passed, 1 warning in 0.22s
1 passed, 1 warning in 0.35s
2 passed, 2 warnings in 0.47s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4798 passed, 36 skipped, 12 warnings in 83.84s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 80: Operator Runbook Restart Metadata Guidance

Date: 2026-06-20

### Goal

Make the Batch 79 startup-only restart metadata visible to operators in the migration/runbook flow, not just in tests and audit documents.

### Steps

1. Re-scan the operator runbook for `restart_required`, `reload-boundary`, and startup-only guidance.
2. Identify the missing operator-facing pieces:
   - `--apply --overwrite` reports `applied.restart_required.app_config` when startup-only app config fields change.
   - admin tooling can call `GET /api/config/reload-boundary` to inspect the canonical restart boundary.
3. Update the overwrite review checklist to include `applied.restart_required`.
4. Add a sample `applied.restart_required.app_config` report shape and operator action guidance.
5. Add the admin reload-boundary endpoint example to the DB mode startup section.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-operator-runbook-restart-metadata-review.md`

### Verification

Focused documentation checks:

```bash
rg -n "applied.restart_required|/api/config/reload-boundary|startup_only_prefix" docs/harness-stateless-db-mode-operator-runbook.md
rg -n "^## Batch 7[8-9]|^## Batch 80" docs/harness-stateless-db-mode-implementation-log.md
git diff --check
```

Result:

```text
runbook check found applied.restart_required, /api/config/reload-boundary, and startup_only_prefix entries.
implementation log check found Batch 78, Batch 79, and Batch 80 in order.
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 81: Embedded Client DB Extensions Writes

Date: 2026-06-20

### Goal

Close the embedded Python client API gap where `DeerFlowClient.update_mcp_config()` and `DeerFlowClient.update_skill()` still assumed file-backed `extensions_config.json` writes even when `DEER_FLOW_CONFIG_SOURCE=db` was active.

### Steps

1. Re-scan the current audit and the client update paths for remaining file-backed MCP/skill write surfaces outside Gateway routers.
2. Add a red test proving `DeerFlowClient.update_mcp_config()` in DB mode must not resolve `extensions_config.json`, must preserve existing skill state and extension extras, and must invalidate the cached embedded agent.
3. Add a red test proving `DeerFlowClient.update_skill()` in DB mode must not resolve `extensions_config.json`, must preserve existing MCP servers and extension extras, and must invalidate the cached embedded agent.
4. Add DB-mode branches in both client methods:
   - use `DbExtensionsConfigStore` with the active app config database;
   - load the current revision and write with `expected_revision`;
   - preserve `model_extra` and the untouched half of the extensions payload;
   - set `updated_by` from `get_effective_user_id()`;
   - reload the active extensions config after saving.
5. Reset MCP tools cache after DB-mode MCP config writes so embedded client callers do not keep stale MCP sessions/tools.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/packages/harness/deerflow/client.py`
- `backend/tests/test_client.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-embedded-client-db-extensions-writes-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_client.py::TestMcpConfig::test_update_mcp_config_db_mode_writes_runtime_config tests/test_client.py::TestSkillsManagement::test_update_skill_db_mode_writes_runtime_config -q
```

Result:

```text
2 failed, 1 warning in 0.68s
Failures confirmed the old client path resolved extensions_config.json in DB mode and the skill test was still routed through DB storage rather than the mocked file storage.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_client.py::TestMcpConfig tests/test_client.py::TestSkillsManagement -q
uv --directory backend run ruff check packages/harness/deerflow/client.py tests/test_client.py
```

Result:

```text
11 passed, 1 warning in 0.56s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4800 passed, 36 skipped, 12 warnings in 84.59s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 82: DB Memory Storage Fail-closed Fallback Guard

Date: 2026-06-20

### Goal

Prevent DB/stateless mode from silently falling back to file-backed memory when the configured DB memory storage cannot initialize.

### Steps

1. Re-scan memory storage and updater paths for remaining file-backed escape hatches in DB mode.
2. Identify the risk in `get_memory_storage()`:
   - file mode intentionally falls back to `FileMemoryStorage` when a configured custom storage cannot load;
   - DB mode reused that same fallback path, which could write Memory to local files if `DbMemoryStorage` failed to initialize.
3. Add a red test proving DB mode must raise the original initialization error and leave the memory storage singleton unset instead of returning `FileMemoryStorage`.
4. Change `get_memory_storage()` to compute DB mode once, keep the file-to-DB default mapping, and re-raise storage initialization errors in DB mode.
5. Keep file mode fallback behavior unchanged and verify the existing file-mode fallback tests.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/packages/harness/deerflow/agents/memory/storage.py`
- `backend/tests/test_db_memory_storage.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-db-memory-fail-closed-fallback-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py::test_get_memory_storage_db_mode_does_not_fall_back_to_file_storage -q
```

Result:

```text
1 failed, 1 warning in 0.27s
Failure confirmed DB mode returned FileMemoryStorage instead of raising the DbMemoryStorage initialization error.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_db_memory_storage.py -q
uv --directory backend run pytest tests/test_memory_storage.py::TestGetMemoryStorage -q
uv --directory backend run ruff check packages/harness/deerflow/agents/memory/storage.py tests/test_db_memory_storage.py
```

Result:

```text
10 passed, 1 warning in 0.32s
6 passed, 1 warning in 0.25s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4801 passed, 36 skipped, 12 warnings in 84.25s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 83: Runtime Store OpenAPI Contract Cleanup

Date: 2026-06-20

### Goal

Remove file-only wording from Gateway runtime mutation API contracts so MCP and skill enabled-state updates describe the active extensions configuration source rather than only `extensions_config.json`/`mcp_config.json`.

### Steps

1. Re-scan remaining MCP/skill DB-mode paths after Batch 82:
   - confirm router file writes are behind non-DB branches;
   - confirm first-class MCP DB storage and legacy aggregate migration already exist;
   - identify stale OpenAPI descriptions/docstrings as the next local contract gap.
2. Add a red OpenAPI contract test asserting MCP and skill update endpoint descriptions:
   - mention the active extensions configuration source;
   - do not claim `save to file`;
   - do not mention `mcp_config.json` or `extensions_config.json file`.
3. Update Gateway route descriptions and MCP handler docstring to be storage-source neutral.
4. Keep runtime behavior unchanged:
   - DB mode still writes through `DbExtensionsConfigStore`;
   - file mode still writes the file-backed extensions source.
5. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/app/gateway/routers/mcp.py`
- `backend/app/gateway/routers/skills.py`
- `backend/tests/test_runtime_store_openapi_contract.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-runtime-store-openapi-contract-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_runtime_store_openapi_contract.py -q
```

Result:

```text
1 failed, 1 warning in 0.58s
Failure confirmed the MCP update endpoint still described the operation as "save to file."
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_runtime_store_openapi_contract.py -q
uv --directory backend run ruff check app/gateway/routers/mcp.py app/gateway/routers/skills.py tests/test_runtime_store_openapi_contract.py
```

Result:

```text
1 passed, 1 warning in 0.50s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4802 passed, 36 skipped, 12 warnings in 84.13s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 84: DB MCP Cache Skips File Mtime Resolution

Date: 2026-06-20

### Goal

Ensure DB/stateless mode does not resolve local extensions config file paths while checking MCP tool cache freshness.

### Steps

1. Re-scan remaining MCP DB-mode file-path references after Batch 83.
2. Identify the risk in `deerflow.mcp.cache._is_cache_stale()`:
   - DB mode uses extensions config revision for cache invalidation;
   - the stale check still called `_get_config_mtime()` after revision comparison;
   - `_get_config_mtime()` resolved `DEER_FLOW_EXTENSIONS_CONFIG_PATH`, so a stale/missing file path could break DB-mode MCP cache access.
3. Add a red test proving DB mode ignores a missing `DEER_FLOW_EXTENSIONS_CONFIG_PATH` when the DB revision is unchanged.
4. Update `_get_config_mtime()` to return `None` immediately in DB config mode.
5. Verify existing MCP cache revision reload behavior remains intact.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/packages/harness/deerflow/mcp/cache.py`
- `backend/tests/test_mcp_cache_revision.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-db-mcp-cache-file-mtime-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py::test_db_mcp_cache_stale_check_ignores_missing_file_config_path -q
```

Result:

```text
1 failed, 1 warning in 0.42s
Failure confirmed DB mode still resolved DEER_FLOW_EXTENSIONS_CONFIG_PATH during MCP cache stale checks.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py -q
uv --directory backend run ruff check packages/harness/deerflow/mcp/cache.py tests/test_mcp_cache_revision.py
```

Result:

```text
3 passed, 1 warning in 0.44s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4803 passed, 36 skipped, 12 warnings in 84.02s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 85: Typed Memory Update Save Failure Reason

Date: 2026-06-20

### Goal

Close the Memory updater observability gap where a failed DB/storage save after retry returned only `False`, making stale-save retry exhaustion indistinguishable from other update failures.

### Steps

1. Re-scan the requirement audit and Memory updater review notes after Batch 84.
2. Identify the remaining local Memory gap:
   - DB memory storage rejects stale saves;
   - Memory updater reloads and retries once;
   - callers still had no typed reason when both save attempts failed.
3. Add red tests for:
   - double save failure records a typed `SAVE_RETRY_EXHAUSTED` reason;
   - a later successful update clears the previous failure reason.
4. Add `MemoryUpdateFailureReason` and `MemoryUpdater.last_failure_reason` while preserving the existing `bool` return API.
5. Set the typed reason only when both save attempts fail; clear it on successful first or retry save.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/packages/harness/deerflow/agents/memory/updater.py`
- `backend/tests/test_memory_updater.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-memory-updater-typed-failure-reason-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_memory_updater.py::TestFinalizeCacheIsolation::test_finalize_records_typed_reason_when_retry_save_fails -q
uv --directory backend run pytest tests/test_memory_updater.py::TestFinalizeCacheIsolation::test_finalize_records_typed_reason_when_retry_save_fails tests/test_memory_updater.py::TestFinalizeCacheIsolation::test_finalize_clears_previous_failure_reason_after_success -q
```

Result:

```text
1 error, 1 warning in 0.30s
1 failed, 1 passed, 1 warning in 0.29s
The first failure confirmed the typed reason API did not exist; the second confirmed successful updates did not yet clear an earlier failure reason.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_memory_updater.py -q
uv --directory backend run ruff check packages/harness/deerflow/agents/memory/updater.py tests/test_memory_updater.py
```

Result:

```text
57 passed, 1 warning in 0.29s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4806 passed, 36 skipped, 12 warnings in 83.97s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 86: Skill Package Size Import Preflight

Date: 2026-06-20

### Goal

Close the migration preflight gap where each custom skill support file could be under the per-file DB threshold while the full DB-backed skill package was still too large for the V1 JSON storage shape.

### Steps

1. Re-scan the requirement audit and migration review notes after Batch 85.
2. Identify the remaining import risk:
   - `DEER_FLOW_DB_SKILL_FILE_MAX_BYTES` blocked individual large support files;
   - many small files plus `SKILL.md` could still produce a large `files_json`/`skill_md_text` payload.
3. Add a red dry-run test where two support files are individually below the file threshold but the total package exceeds `DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES`.
4. Add `DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES` with a conservative default and shared non-negative integer env parsing.
5. Record `package_bytes` in each skill report and surface `skill-package-too-large` as a preflight error.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-skill-package-size-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package -q
```

Result:

```text
1 failed, 1 warning in 0.20s
Failure confirmed package-level skill DB-size risk was not reported.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
2 passed, 1 warning in 0.17s
23 passed, 1 warning in 1.80s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4807 passed, 36 skipped, 12 warnings in 84.12s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 87: Structured Runtime-State Import Preflight Error Codes

Date: 2026-06-20

### Goal

Close the migration preflight diagnostics gap where operator-visible failures were only human-readable strings, making scripted remediation depend on brittle text matching.

### Steps

1. Re-scan the requirement audit and migration review notes after Batch 86.
2. Identify the remaining local migration gap:
   - preflight already blocks invalid app config, extensions config, agent config, memory JSON, and oversized custom skill payloads;
   - the `preflight.errors` entries did not expose stable machine-readable reason codes.
3. Add red assertions that source/preflight errors include `code` for:
   - invalid memory;
   - invalid app config;
   - invalid extensions config;
   - invalid agent config;
   - oversized skill support file;
   - oversized skill package.
4. Add stable error codes to `_collect_source_errors()` while preserving existing `resource`, `path`, and human-readable `error` fields.
5. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-runtime-state-import-error-codes-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_invalid_memory_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_agent_config_semantics -q
```

Result:

```text
6 failed, 1 warning in 0.23s
Failures confirmed preflight errors did not yet include stable code fields.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_invalid_memory_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_app_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_package tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_agent_config_semantics -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
6 passed, 1 warning in 0.17s
23 passed, 1 warning in 1.76s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4807 passed, 36 skipped, 12 warnings in 84.85s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 88: Runtime-State Import CLI Clean Stderr Contract

Date: 2026-06-20

### Goal

Close the migration CLI scripting gap where successful `--apply --overwrite` subprocess runs could emit unrelated runtime/config warnings to stderr while still returning a valid JSON report on stdout.

### Steps

1. Re-scan the requirement audit and migration CLI review notes after Batch 87.
2. Identify the remaining local CLI gap:
   - `--help` and invalid `--database-url` had explicit diagnostics coverage;
   - successful apply subprocess tests still tolerated stderr warning noise.
3. Add red assertions requiring successful CLI apply subprocess runs to produce empty stderr for both:
   - `DEER_FLOW_DATABASE_URL` bootstrap selection;
   - explicit `--database-url` selection.
4. Add a CLI-only runtime-noise suppression boundary around `main()`:
   - capture Python warnings during the migration CLI run;
   - temporarily raise the `deerflow.config.app_config` logger to `ERROR`;
   - restore the logger level even when the CLI exits through an error path.
5. Preserve argparse diagnostics and the Python `import_runtime_state_to_db()` API behavior.
6. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-runtime-state-import-cli-clean-stderr-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_uses_bootstrap_database_url tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_accepts_database_url_argument -q
```

Result:

```text
2 failed, 1 warning in 1.13s
Failures confirmed successful CLI apply subprocesses still emitted runtime/config warning noise on stderr.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_uses_bootstrap_database_url tests/test_import_runtime_state_to_db.py::test_import_runtime_state_cli_apply_overwrite_accepts_database_url_argument -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
2 passed, 1 warning in 1.11s
23 passed, 1 warning in 1.81s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4807 passed, 36 skipped, 12 warnings in 84.98s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 89: Content-sensitive Sandbox Runtime Context Revision

Date: 2026-06-20

### Goal

Close the local sandbox runtime-context revision gap where `SandboxRuntimeContextManifestBuilder` produced revisions from user, agent, and requested skill names, while content changes were only visible through the lower-level manifest hash.

### Steps

1. Re-scan the requirement audit and sandbox materializer review notes after Batch 88.
2. Identify the remaining local sandbox gap:
   - manifest hash already made materialization no-op/diff content-sensitive;
   - the builder-level `revision` did not change when DB-backed memory, agent, or skill contents changed under the same user/agent/skill selection.
3. Add a red test that builds two runtime-context manifests for the same user after changing memory content and expects different revisions.
4. Add a deterministic file-content snapshot hash to the builder revision while preserving existing user/agent/skills revision labels.
5. Add this batch review document and refresh the requirement audit.

### Files Changed

- `backend/packages/harness/deerflow/sandbox/materializer.py`
- `backend/tests/test_sandbox_materializer.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-sandbox-runtime-context-revision-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_revision_changes_when_content_changes -q
```

Result:

```text
1 failed, 1 warning in 0.32s
Failure confirmed runtime-context revisions were unchanged when content changed under the same user selection.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_runtime_context_manifest_builder_revision_changes_when_content_changes -q
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
uv --directory backend run ruff check packages/harness/deerflow/sandbox/materializer.py tests/test_sandbox_materializer.py
```

Result:

```text
1 passed, 1 warning in 0.26s
11 passed, 1 warning in 0.34s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4808 passed, 36 skipped, 12 warnings in 84.44s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 90: Public Skill Deployment Artifact Boundary Reporting

Date: 2026-06-20

### Goal

Make the V1 public-skill storage boundary operationally explicit in the runtime-state import report. Public skills still remain immutable deployment artifacts in V1, but each public skill row now carries the artifact requirement that operators must verify during rollout.

### Steps

1. Re-scan the requirement audit, V1 scope decision, operator runbook, and runtime-state import reporting code.
2. Confirm that public skills were already reported as `import_action: file-backed-skip`, but the report did not say which storage boundary owned them or which deployment artifact was required.
3. Add a red assertion to `test_import_runtime_state_apply_writes_custom_skills` requiring:
   - custom skills: `storage_boundary: db`, `runtime_artifact_required: false`;
   - public skills: `storage_boundary: deployment-artifact`, `runtime_artifact_required: true`, `deployment_artifact: gateway-public-skill-bundle`.
4. Add the stable report fields to every skill inventory row.
5. Refresh the operator runbook, V1 scope decision doc, requirement audit, and this batch review document.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-v1-scope-decisions.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-public-skill-deployment-artifact-boundary-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_custom_skills -q
```

Result:

```text
1 failed, 1 warning in 0.36s
Failure confirmed skill inventory rows did not expose storage_boundary or runtime artifact requirements.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_custom_skills -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
1 passed, 1 warning in 0.30s
23 passed, 1 warning in 1.91s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4808 passed, 36 skipped, 12 warnings in 84.19s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 91: Provisioner Existing Pod Mount Contract Guard

Date: 2026-06-20

### Goal

Close a remote provisioner correctness gap where `POST /api/sandboxes` returned an existing Pod solely by `sandbox_id`. In DB/stateless mode, the same deterministic sandbox id can be reused after the required `skills.container_path` mount contract changes, so returning an old Pod with missing or read-only `/mnt/skills` would defer the failure until runtime-context materialization.

### Steps

1. Re-scan the remote AIO smoke, `RemoteSandboxBackend`, bundled provisioner, provisioner PVC tests, and runbook notes.
2. Identify the idempotent create risk:
   - gateway sends `extra_mounts` for DB-mode writable skills path;
   - bundled provisioner consumes `extra_mounts` for new Pods;
   - existing Pods were returned without checking whether their mount contract matched the current request.
3. Add a red test proving an existing Pod with a mismatched mount contract must raise HTTP 409 instead of being silently reused.
4. Add mount-contract annotations to new Pods and compare the stored contract hash before returning an existing Pod.
5. Add matching-contract and annotation tests, then refresh this batch review and the requirement audit.

### Files Changed

- `docker/provisioner/app.py`
- `backend/tests/test_provisioner_pvc_volumes.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-provisioner-existing-pod-mount-contract-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::test_create_sandbox_rejects_existing_pod_with_mismatched_mount_contract -q
```

Result:

```text
1 failed, 1 warning in 0.39s
Failure confirmed existing Pods were reused without checking the requested mount contract.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::test_create_sandbox_rejects_existing_pod_with_mismatched_mount_contract -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py tests/test_aio_sandbox_provider.py::test_remote_backend_create_forwards_extra_mounts -q
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check ../docker/provisioner/app.py tests/test_provisioner_pvc_volumes.py
```

Result:

```text
1 passed, 1 warning in 0.33s
26 passed, 1 warning in 0.39s
27 passed, 1 warning in 0.44s
1 skipped, 1 warning in 0.19s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4811 passed, 36 skipped, 12 warnings in 85.26s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 92: Remote Provisioner Create Error Detail Propagation

Date: 2026-06-20

### Goal

Make remote provisioner create failures actionable from the gateway/test side. Batch 91 made bundled provisioner mount-contract mismatches return HTTP 409 with a useful response body, but `RemoteSandboxBackend` only surfaced the HTTP error summary, so operators could lose the actual remediation detail during remote live smoke runs.

### Steps

1. Re-scan `RemoteSandboxBackend`, remote backend tests, Batch 91 behavior, and remote AIO runbook guidance.
2. Add a red test where the provisioner returns HTTP 409 with a mount-contract remediation body.
3. Confirm the old RuntimeError only included `HTTP 409` and dropped the response body.
4. Include `exc.response.text` in `_provisioner_create()` RuntimeError/log details when available.
5. Refresh the implementation log, review document, and requirement audit.

### Files Changed

- `backend/packages/harness/deerflow/community/aio_sandbox/remote_backend.py`
- `backend/tests/test_remote_sandbox_backend.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-remote-provisioner-create-error-detail-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_remote_sandbox_backend.py::test_provisioner_create_includes_response_body_in_runtime_error -q
```

Result:

```text
1 failed, 1 warning in 0.21s
Failure confirmed the RuntimeError omitted the provisioner response body.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_remote_sandbox_backend.py::test_provisioner_create_includes_response_body_in_runtime_error -q
uv --directory backend run pytest tests/test_remote_sandbox_backend.py -q
uv --directory backend run ruff check packages/harness/deerflow/community/aio_sandbox/remote_backend.py tests/test_remote_sandbox_backend.py
```

Result:

```text
1 passed, 1 warning in 0.19s
23 passed, 1 warning in 0.19s
All checks passed!
```

Remote live default workstation check:

```bash
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
```

Result:

```text
1 skipped, 1 warning in 0.19s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4812 passed, 36 skipped, 12 warnings in 87.94s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 93: Remote Provisioner Anonymous Thread Payload Compatibility

Date: 2026-06-20

### Goal

Close a gateway/provisioner payload mismatch for anonymous AIO sandbox creation. `AioSandboxProvider.acquire()` supports `thread_id=None`, and `RemoteSandboxBackend` had a test for anonymous creation, but the bundled provisioner request model requires `thread_id` to be a string matching `SAFE_THREAD_ID_PATTERN`.

### Steps

1. Re-scan `AioSandboxProvider`, `RemoteSandboxBackend`, bundled provisioner `CreateSandboxRequest`, and existing remote backend tests.
2. Confirm the mismatch:
   - provider/backend allow `thread_id=None`;
   - bundled provisioner rejects `thread_id: null`;
   - anonymous remote sandbox creation would fail before Pod creation.
3. Change the anonymous create test to expect the backend to send `sandbox_id` as the provisioner `thread_id` fallback.
4. Update `RemoteSandboxBackend._provisioner_create()` to send `thread_id or sandbox_id`.
5. Refresh this batch review and the requirement audit.
6. Run focused remote backend/live-entrypoint checks and the full backend regression suite.

### Files Changed

- `backend/packages/harness/deerflow/community/aio_sandbox/remote_backend.py`
- `backend/tests/test_remote_sandbox_backend.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-remote-provisioner-anonymous-thread-payload-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_remote_sandbox_backend.py::test_provisioner_create_accepts_anonymous_thread_id -q
```

Result:

```text
1 failed, 1 warning in 0.30s
Failure confirmed the provisioner payload still sent `thread_id: None`.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_remote_sandbox_backend.py::test_provisioner_create_accepts_anonymous_thread_id -q
uv --directory backend run pytest tests/test_remote_sandbox_backend.py -q
uv --directory backend run ruff check packages/harness/deerflow/community/aio_sandbox/remote_backend.py tests/test_remote_sandbox_backend.py
```

Result:

```text
1 passed, 1 warning in 0.20s
23 passed, 1 warning in 0.21s
All checks passed!
```

Remote focused aggregate:

```bash
uv --directory backend run pytest tests/test_remote_sandbox_backend.py tests/test_aio_sandbox_remote_live.py -q
```

Result:

```text
23 passed, 1 skipped, 1 warning in 0.24s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4812 passed, 36 skipped, 12 warnings in 90.23s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run pytest -m remote_live -q` in the real provisioner/K8s environment with the required remote AIO environment variables.
- Execute `uv --directory backend run pytest -m live -q` or `uv --directory backend run pytest -m requires_llm -q` in an environment with real model config and any required live service credentials.

---

## Batch 94: Stateless Live Gate Preflight CLI

Date: 2026-06-20

### Goal

Make the remaining stateless rollout live gates easier to execute and audit. The remote provisioner/K8s and model-backed gates still require real external environments, but operators should be able to machine-check whether the selected gate has the required environment variables and see the exact pytest command before running it.

### Steps

1. Re-scan the operator runbook, remote live smoke test, pytest live markers, and current requirement audit.
2. Add a failing test suite for a new live-gate preflight CLI:
   - remote live gate reports missing host path/prefix;
   - remote live gate accepts host path prefix;
   - JSON CLI exits nonzero when the gate is not ready;
   - `--run` refuses to invoke pytest when required environment is missing.
3. Implement `scripts/check_stateless_live_gates.py` with deterministic gate specs for `remote_live`, `docker_live`, and `requires_llm`.
4. Run the live-marker regression guard and fix its detector to avoid treating unit-test string assertions about live env names as real live-gate opt-ins.
5. Document the preflight entrypoint in the operator runbook.
6. Add this batch review and refresh the requirement audit.
7. Run focused live-gate checks and the full backend regression suite.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `backend/tests/test_live_gate_markers.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-stateless-live-gate-preflight-cli-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py::test_live_gate_marker_detector_ignores_documented_env_var_strings -q
```

Result:

```text
1 error in 0.06s
Failure confirmed `backend/scripts/check_stateless_live_gates.py` did not exist yet.

1 failed, 1 warning in 0.21s
Failure confirmed the live marker detector treated unit-test env-name strings as remote live opt-ins.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py
```

Result:

```text
4 passed, 1 warning in 0.17s
3 passed, 1 warning in 0.93s
7 passed, 1 skipped, 1 warning in 0.99s
All checks passed!
```

Default workstation preflight:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --json
```

Result:

```text
exit code 2
missing_env included DEER_FLOW_RUN_REMOTE_AIO_SANDBOX, DEER_FLOW_REMOTE_AIO_PROVISIONER_URL, and DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH or DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4817 passed, 36 skipped, 12 warnings in 90.05s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run` in the real provisioner/K8s environment after the script reports `ok: true`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` or the raw `pytest -m requires_llm` command in an environment with real model config and credentials.

---

## Batch 95: Requires LLM Live Gate Config Preflight

Date: 2026-06-20

### Goal

Close a preflight false-ready condition for model-backed live gates. Batch 94 added `requires_llm` to the stateless live-gate preflight CLI, but because that gate had no required environment variables, the CLI reported `ok: true` even when the local `config.yaml` had no configured models and `tests/test_client_live.py` would skip.

### Steps

1. Re-scan `scripts/check_stateless_live_gates.py`, `tests/test_client_live.py`, and current runbook/audit wording.
2. Confirm the mismatch with:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
```

3. Add failing tests for `requires_llm` preflight:
   - missing `config.yaml`;
   - empty `models` section;
   - valid minimal model declaration.
4. Add `requires_model_config` gate metadata and `config_issues` reporting.
5. Update runbook, requirement audit, and this batch review.
6. Run focused live-gate checks and the full backend regression suite.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-live-gate-config-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_requires_config_yaml_with_models tests/test_stateless_live_gate_check.py::test_requires_llm_gate_rejects_empty_models_config tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_config_with_models -q
```

Result:

```text
3 failed, 1 warning in 0.22s
Failure confirmed `build_gate_report()` had no `project_root` support and the LLM gate did not inspect model config.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_requires_config_yaml_with_models tests/test_stateless_live_gate_check.py::test_requires_llm_gate_rejects_empty_models_config tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_config_with_models -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
3 passed, 1 warning in 0.17s
7 passed, 1 warning in 0.19s
10 passed, 1 skipped, 1 warning in 0.97s
All checks passed!
```

Default workstation preflight:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
```

Result:

```text
exit code 2
config_issues included `config.yaml has no configured models`.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4820 passed, 36 skipped, 12 warnings in 88.23s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` in an environment where `config.yaml` declares at least one real model and provider credentials are valid.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 96: Requires LLM Preflight DB Config Source Awareness

Date: 2026-06-20

### Goal

Align the model-backed live-gate preflight with stateless DB mode. Batch 95 made `requires_llm` inspect file-mode `config.yaml`, but in `DEER_FLOW_CONFIG_SOURCE=db` deployments the app config source of truth is `runtime_configs.app`, so the preflight could incorrectly fail a valid stateless setup that has no local config file.

### Steps

1. Re-scan DB runtime config repository/model/source code and the existing live-gate preflight.
2. Add failing tests for DB config-source behavior:
   - DB mode with `runtime_configs.app.models` should be ready without local `config.yaml`;
   - DB mode without `DEER_FLOW_DATABASE_URL` should report an explicit config issue.
3. Implement DB-aware `requires_llm` checks:
   - honor `DEER_FLOW_CONFIG_SOURCE=db`;
   - require `DEER_FLOW_DATABASE_URL`;
   - read `runtime_configs.app`;
   - validate the app config payload's `models` list using the same minimal model-entry test as file mode.
4. Update runbook, requirement audit, and this batch review.
5. Run focused live-gate checks and the full backend regression suite.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-db-config-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_db_config_with_models tests/test_stateless_live_gate_check.py::test_requires_llm_gate_db_mode_requires_database_url -q
```

Result:

```text
2 failed, 1 warning in 0.20s
Failure confirmed the preflight still fell back to file-mode config checks in DB config mode.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_db_config_with_models tests/test_stateless_live_gate_check.py::test_requires_llm_gate_db_mode_requires_database_url -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.18s
9 passed, 1 warning in 0.21s
12 passed, 1 skipped, 1 warning in 0.96s
All checks passed!
```

Default workstation preflight:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
```

Result:

```text
exit code 2
config_issues included `config.yaml has no configured models`.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4822 passed, 36 skipped, 12 warnings in 87.59s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 103: Remote Live Direct Entrypoint Env Validation

Date: 2026-06-20

### Goal

Close the direct-pytest gap left after Batch 102. The preflight CLI rejected malformed remote-live environment values, but an operator could still run `tests/test_aio_sandbox_remote_live.py` directly and see late failures such as a bare `ValueError` for `DEER_FLOW_REMOTE_AIO_READY_TIMEOUT=soon` or a request-layer error for a malformed provisioner URL.

### Steps

1. Re-check `tests/test_aio_sandbox_remote_live.py` and the Batch 102 preflight helper.
2. Add a failing test proving the direct remote live test module exposes a readable invalid-env message before backend creation.
3. Implement `_invalid_remote_live_env_message()` in the remote live test module by reusing `check_stateless_live_gates._remote_live_env_issues()`.
4. Call that helper after opt-in and before creating `RemoteSandboxBackend`.
5. Update the operator runbook, requirement audit, implementation log, and batch review.
6. Run focused checks and full backend verification.

### Files Changed

- `backend/tests/test_aio_sandbox_remote_live.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-remote-live-direct-entrypoint-env-validation-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_remote_live_pytest_entrypoint_reports_invalid_env_before_backend_create -q
```

Result:

```text
1 failed, 1 warning in 0.19s
Failure confirmed the remote live test module had no `_invalid_remote_live_env_message`.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check tests/test_aio_sandbox_remote_live.py tests/test_stateless_live_gate_check.py
```

Result:

```text
16 passed, 1 warning in 0.20s
1 skipped, 1 warning in 0.17s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4836 passed, 36 skipped, 12 warnings in 86.09s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 101: Channel Runtime DB Store And Migration Apply

Date: 2026-06-20

### Goal

Close the remaining stateless gap for UI-entered IM channel runtime credentials. Batch 100 made `.deer-flow/channels/runtime-config.json` visible to migration dry-run; this batch makes DB mode read/write `runtime_configs.channel_runtime` at runtime and makes migration apply import the same source file.

### Steps

1. Re-audit `ChannelRuntimeConfigStore`, channel connection router, and `ChannelService.from_app_config()` call paths.
2. Add failing tests for:
   - DB-mode store factory selection without creating local runtime JSON;
   - default runtime channel merge loading DB-backed provider config;
   - migration apply writing `runtime_configs.channel_runtime`;
   - preflight conflict detection for existing `channel_runtime` rows.
3. Implement `DbChannelRuntimeConfigStore` using the existing provider-keyed payload shape in `runtime_configs.channel_runtime`.
4. Add `get_channel_runtime_config_store()` and route DB mode through it while preserving file-mode compatibility.
5. Offload router-side runtime config merge/apply calls with `asyncio.to_thread()` so DB reads stay off the event loop.
6. Extend runtime-state import apply and conflict detection for channel runtime config.
7. Update the technical design, operator runbook, requirement audit, implementation log, and batch review.
8. Run focused checks and full backend verification.

### Files Changed

- `backend/app/channels/runtime_config_store.py`
- `backend/app/gateway/routers/channel_connections.py`
- `backend/app/channels/service.py`
- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/blocking_io/test_channel_runtime_config_store.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-channel-runtime-db-store-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/blocking_io/test_channel_runtime_config_store.py::test_runtime_config_store_factory_uses_db_in_db_config_mode tests/blocking_io/test_channel_runtime_config_store.py::test_merge_runtime_channel_configs_loads_db_store_by_default -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_channel_runtime_config tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_channel_runtime_conflict -q
```

Result:

```text
2 failed, 1 warning in 0.52s
2 failed, 1 warning in 0.39s
```

Green focused checks:

```bash
uv --directory backend run pytest tests/blocking_io/test_channel_runtime_config_store.py -q
uv --directory backend run pytest tests/test_channel_connections_router.py -q
uv --directory backend run pytest tests/test_channels.py -k "from_app_config" -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check app/channels/runtime_config_store.py app/gateway/routers/channel_connections.py app/channels/service.py scripts/import_runtime_state_to_db.py tests/blocking_io/test_channel_runtime_config_store.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
7 passed, 1 warning in 0.71s
30 passed, 2 warnings in 1.42s
5 passed, 208 deselected, 1 warning in 0.62s
28 passed, 1 warning in 2.22s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4832 passed, 36 skipped, 12 warnings in 86.22s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.
- Revisit aggregate-row write concurrency for `runtime_configs.channel_runtime` if channel credential edits become high-frequency or multi-admin concurrent workflows.

---

## Batch 102: Remote Live Gate Env Shape Preflight

Date: 2026-06-20

### Goal

Make the remote provisioner/K8s live-gate preflight stricter before it reports a gate as ready to invoke. The previous preflight checked required env variables for presence, but malformed values such as `DEER_FLOW_REMOTE_AIO_PROVISIONER_URL=provisioner:8002` or relative host/container paths could still be reported as ready and fail only after pytest started.

### Steps

1. Re-read `scripts/check_stateless_live_gates.py`, `tests/test_stateless_live_gate_check.py`, and `tests/test_aio_sandbox_remote_live.py`.
2. Add failing tests for:
   - malformed provisioner URLs;
   - relative `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX`;
   - relative `DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH`;
   - non-integer ready timeout.
3. Add remote-live-specific `invalid_env` validation while keeping the existing JSON report shape.
4. Update the operator runbook, requirement audit, implementation log, and batch review.
5. Run focused checks and full backend verification.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-remote-live-env-shape-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_remote_live_gate_rejects_invalid_provisioner_url tests/test_stateless_live_gate_check.py::test_remote_live_gate_rejects_relative_host_path_prefix tests/test_stateless_live_gate_check.py::test_remote_live_gate_rejects_relative_container_path_and_bad_timeout -q
```

Result:

```text
3 failed, 1 warning in 0.19s
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
15 passed, 1 warning in 0.18s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4835 passed, 36 skipped, 12 warnings in 86.08s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 97: Requires LLM Model Env Reference Preflight

Date: 2026-06-20

### Goal

Close one remaining false-ready path in the model-backed live-gate preflight. Batch 96 proved that `requires_llm` reads the active file or DB app config source, but a model entry like `api_key: $OPENAI_API_KEY` was still considered ready even when `OPENAI_API_KEY` was absent from the process environment. That would cause `--run` to invoke tests that later fail or skip during config env resolution.

### Steps

1. Confirm `AppConfig.resolve_env_variables()` raises when a config value starts with `$` and the named environment variable is missing.
2. Add failing tests for missing model env references in both file-mode `config.yaml` and DB-mode `runtime_configs.app`.
3. Update positive file/DB model-config tests to provide `OPENAI_API_KEY`, making the credential precondition explicit.
4. Implement shared model-payload readiness:
   - keep structural config failures in `config_issues`;
   - recursively scan valid model entries for strings that start with `$`, matching `AppConfig.resolve_env_variables()`;
   - report absent or empty referenced variables in `missing_env`;
   - preserve file and DB source selection behavior.
5. Update runbook, requirement audit, and this batch review.
6. Run focused checks, then the full backend regression suite.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-model-env-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_missing_file_model_env_reference tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_missing_db_model_env_reference -q
```

Result:

```text
2 failed, 1 warning in 0.22s
Failures confirmed file and DB model configs with `api_key: $OPENAI_API_KEY` were still reported ready when `OPENAI_API_KEY` was missing.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_missing_file_model_env_reference tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_nested_file_model_env_reference tests/test_stateless_live_gate_check.py::test_requires_llm_gate_reports_missing_db_model_env_reference -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_live_gate_markers.py tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
3 passed, 1 warning in 0.18s
12 passed, 1 warning in 0.18s
15 passed, 1 skipped, 1 warning in 0.88s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4825 passed, 36 skipped, 12 warnings in 85.41s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 98: Migration MCP Env Reference Risk Reporting

Date: 2026-06-20

### Goal

Close the remaining local dry-run reporting gap from the original migration plan. The migration inventory already reported stdio MCP statefulness, resolved plaintext secrets, and invalid schemas, but it did not explicitly report MCP fields that still reference deployment environment variables such as `headers.Authorization: $MCP_AUTH_HEADER`.

### Steps

1. Re-read the plan and technical design migration requirements for dry-run risk reporting.
2. Inspect `scripts/import_runtime_state_to_db.py` and the existing migration inventory tests.
3. Add a failing assertion to the source/risk inventory test for an MCP header env reference.
4. Implement shared secret-field risk reporting:
   - values that start with `$` emit non-blocking `env-ref` risk entries with the env variable name;
   - non-empty literal values still emit `resolved-secret` risk entries;
   - stdio MCP risks and schema preflight errors remain unchanged.
5. Update the plan checklist, operator runbook, requirement audit, implementation log, and batch review.
6. Run focused migration checks, then full backend verification.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-plan.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-migration-mcp-env-ref-risk-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks -q
```

Result:

```text
1 failed, 1 warning in 0.21s
Failure confirmed `extensions.risks` did not include an `env-ref` entry for `headers.Authorization: $MCP_AUTH_HEADER`.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
1 passed, 1 warning in 0.16s
23 passed, 1 warning in 1.84s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4825 passed, 36 skipped, 12 warnings in 84.77s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 99: Migration App Config Secret Risk Reporting

Date: 2026-06-20

### Goal

Close the app-config side of the migration dry-run secret-risk reporting requirement. Batch 98 made MCP env references visible, but `_config_report()` still did not report `$ENV_NAME` or literal secret values inside `config.yaml`, including model provider credentials such as `models[].api_key`.

### Steps

1. Re-check the migration plan and technical design requirement for secret env refs and resolved-secret risks.
2. Inspect `_config_report()` and confirm it only reported source presence, top-level keys, and YAML errors.
3. Add a failing test for app config model credentials:
   - `api_key: $OPENAI_API_KEY` should report `env-ref`;
   - `api_key: literal-secret` should report `resolved-secret`;
   - both should be included in `summary.risks`.
4. Implement app config secret-risk scanning:
   - add `config.risks`;
   - recursively scan fields whose names indicate secrets, tokens, passwords, API keys, or authorization headers;
   - reuse the same `env-ref` and `resolved-secret` semantics as MCP risk reporting;
   - include `config.risks` in the summary risk count.
5. Update the operator runbook, requirement audit, implementation log, and batch review.
6. Run focused migration checks, then full backend verification.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-migration-app-config-secret-risk-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_app_config_secret_risks -q
```

Result:

```text
1 failed, 1 warning in 0.20s
Failure confirmed `config.risks` was missing from the app config inventory report.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_app_config_secret_risks -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
1 passed, 1 warning in 0.16s
24 passed, 1 warning in 1.81s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4826 passed, 36 skipped, 12 warnings in 88.86s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 100: Migration Channel Runtime Inventory

Date: 2026-06-20

### Goal

Close the migration dry-run gap where UI-entered IM channel runtime config at `.deer-flow/channels/runtime-config.json` was not discovered, counted, or validated before DB import. The technical design explicitly requires dry-run reporting for channel runtime config, and the runtime store is file-backed state that matters for stateless rollout.

### Steps

1. Re-check the technical design dry-run reporting requirements.
2. Inspect `ChannelRuntimeConfigStore` and confirm the default file path is `.deer-flow/channels/runtime-config.json`.
3. Add failing tests for:
   - channel runtime inventory discovery with provider summaries;
   - resolved-secret risk reporting for runtime credentials such as `slack.bot_token`;
   - invalid channel runtime JSON reported as a blocking preflight error without creating a DB file.
4. Implement `channel_runtime` inventory reporting:
   - report source path and provider summaries;
   - scan secret-like fields for `env-ref` / `resolved-secret` risks;
   - include `channel_runtime_configs` and channel runtime risks in `summary`;
   - add `invalid_channel_runtime_config` to source preflight errors.
5. Update the operator runbook, requirement audit, implementation log, and batch review.
6. Run focused migration checks, then full backend verification.

### Files Changed

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-migration-channel-runtime-inventory-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_channel_runtime_config tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_invalid_channel_runtime_config -q
```

Result:

```text
2 failed, 1 warning in 0.25s
Failures confirmed `channel_runtime.risks` was missing and invalid channel runtime JSON did not fail preflight.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_channel_runtime_config tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_invalid_channel_runtime_config -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Result:

```text
2 passed, 1 warning in 0.21s
26 passed, 1 warning in 2.16s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4828 passed, 36 skipped, 12 warnings in 86.41s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 104: Requires LLM DB Live Client Entrypoint

Date: 2026-06-20

### Goal

Close the remaining direct-entrypoint gap for model-backed live client tests in DB/stateless mode. Batches 95-97 made `scripts/check_stateless_live_gates.py --gate requires_llm` read DB-backed `runtime_configs.app` and report model/env readiness, but direct execution of `tests/test_client_live.py` still called synchronous `get_app_config()` before the DB startup preload. A valid DB-mode model config could therefore be skipped with `DB config mode requires load_and_cache_db_app_config()`.

### Steps

1. Re-check the `requires_llm` preflight path and the module-level skip logic in `tests/test_client_live.py`.
2. Add a failing test that imports `tests/test_client_live.py` with `DEER_FLOW_CONFIG_SOURCE=db` and a seeded `runtime_configs.app` row.
3. Extract `load_and_cache_bootstrap_db_app_config()` so gateway startup and direct live-test entrypoints share the same bootstrap DB AppConfig loading path without importing gateway routers.
4. Implement a DB-mode live-test readiness path that calls the shared bootstrap loader before checking configured models.
5. Delay `DeerFlowClient` / `StreamEvent` imports until after module-level readiness so DB-mode collection does not log pre-preload config errors.
6. Preserve file-mode skip behavior for CI, missing `config.yaml`, and empty file-mode models.
7. Add a review document and update the requirement audit.
8. Run focused checks and full backend verification.

### Files Changed

- `backend/packages/harness/deerflow/config/app_config.py`
- `backend/app/gateway/app.py`
- `backend/tests/test_client_live.py`
- `backend/tests/test_client_live_db_mode_readiness.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-db-live-client-entrypoint-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py -q
```

Result:

```text
1 failed, 1 warning in 0.30s
Failure confirmed `tests/test_client_live.py` appended a skip marker in DB mode because `get_app_config()` had not been preloaded from DB.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py tests/test_gateway_db_config_startup.py -q
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_client_live.py -q
uv --directory backend run ruff check packages/harness/deerflow/config/app_config.py app/gateway/app.py tests/test_client_live.py tests/test_client_live_db_mode_readiness.py tests/test_gateway_db_config_startup.py
uv --directory backend run ruff check tests/test_client_live.py tests/test_client_live_db_mode_readiness.py tests/test_stateless_live_gate_check.py
```

Result:

```text
3 passed, 1 warning in 0.70s
17 passed, 1 warning in 0.60s
19 skipped, 1 warning in 0.13s
All checks passed!
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4837 passed, 36 skipped, 12 warnings in 89.08s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 105: Requires LLM File Config Path Preflight

Date: 2026-06-20

### Goal

Close the remaining file-mode preflight mismatch for model-backed live gates. Runtime `AppConfig.resolve_config_path()` supports `DEER_FLOW_CONFIG_PATH`, but `scripts/check_stateless_live_gates.py --gate requires_llm` only inspected project-root `config.yaml`. Operators using an explicit config path could therefore be falsely blocked by the preflight even though the runtime would load the configured file.

### Steps

1. Re-check `check_stateless_live_gates.py` and the runtime `AppConfig.resolve_config_path()` behavior.
2. Add a failing test where `DEER_FLOW_CONFIG_PATH` points to a valid model config while the project root has no `config.yaml`.
3. Make file-mode `requires_llm` preflight prefer `DEER_FLOW_CONFIG_PATH` when set and otherwise keep the existing project-root `config.yaml` fallback.
4. Update operator runbook, requirement audit, implementation log, and live-gate evidence review docs.
5. Run focused checks and full backend verification.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-file-config-path-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_deer_flow_config_path -q
```

Result:

```text
1 failed, 1 warning in 0.23s
Failure confirmed file-mode preflight ignored `DEER_FLOW_CONFIG_PATH` and reported the gate as not ready.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_deer_flow_config_path -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.20s
17 passed, 1 warning in 0.22s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4838 passed, 36 skipped, 12 warnings in 89.01s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 106: Requires LLM DB Bootstrap URL Preflight

Date: 2026-06-20

### Goal

Close the DB-mode live-gate preflight mismatch for sqlite bootstrap URLs. Runtime startup parses `DEER_FLOW_DATABASE_URL` through `database_config_from_url()` and, for sqlite, derives `sqlite_dir` from the URL path parent while using the standard `{sqlite_dir}/deerflow.db` file. The preflight script was reading the sqlite URL file directly, so a URL such as `sqlite:///.../bootstrap-placeholder.db` could make preflight inspect a different DB than runtime startup.

### Steps

1. Re-check `database_config_from_url()` and `DatabaseConfig.app_sqlalchemy_url` for sqlite bootstrap semantics.
2. Add a failing test where the runtime app config row exists in `{sqlite_dir}/deerflow.db` but `DEER_FLOW_DATABASE_URL` points at another sqlite filename in the same directory.
3. Make DB-mode preflight derive the actual sync SQLAlchemy URL through `database_config_from_url()` instead of manually converting the env URL.
4. Update existing DB-mode live-gate tests to use the runtime-standard `deerflow.db` filename.
5. Update operator runbook, requirement audit, implementation log, and batch review.
6. Run focused checks and full backend verification.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-db-bootstrap-url-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_uses_bootstrap_sqlite_database_path -q
```

Result:

```text
1 failed, 1 warning in 0.22s
Failure confirmed DB-mode preflight inspected the sqlite URL file directly instead of the runtime bootstrap `deerflow.db` path.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_uses_bootstrap_sqlite_database_path -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.22s
18 passed, 1 warning in 0.24s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4839 passed, 36 skipped, 12 warnings in 85.01s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 107: Requires LLM Direct E2E Entrypoint Readiness

Date: 2026-06-20

### Goal

Close the remaining direct `requires_llm` pytest-entrypoint mismatch after the preflight work. `test_client_e2e.py` and `test_create_deerflow_agent_live.py` still skipped real-LLM tests based on `OPENAI_API_KEY` and, in the agent-factory live test, hand-built a `ChatOpenAI` instance from E2E env vars. A valid file-mode or DB-mode model config using another credential env reference could therefore pass preflight but still be skipped or bypass the active AppConfig at direct pytest runtime.

### Steps

1. Inspect the remaining `requires_llm` live entrypoints and confirm which ones are generic runtime-config gates versus intentionally OneAPI-specific gates.
2. Add failing tests for a provider-neutral model config that references `$AZURE_OPENAI_API_KEY` while `OPENAI_API_KEY` is empty.
3. Add `tests/support/live_gate_readiness.py` so direct pytest decorators can reuse `scripts/check_stateless_live_gates.py` `requires_llm` readiness logic.
4. Update `test_create_deerflow_agent_live.py` to create its model through `deerflow.models.create_chat_model()` with the active file/DB AppConfig.
5. Update `test_client_e2e.py` to use the active AppConfig when live readiness passes, while preserving the legacy synthetic config fallback for non-LLM E2E checks in unconfigured workstations.
6. Update the live-marker static guard so the shared `mark_requires_llm` helper is recognized as applying `live` and `requires_llm` markers.
7. Update operator runbook, requirement audit, implementation log, and batch review.
8. Run focused checks and full backend verification.

### Files Changed

- `backend/tests/support/live_gate_readiness.py`
- `backend/tests/test_requires_llm_live_entrypoint_readiness.py`
- `backend/tests/test_client_e2e.py`
- `backend/tests/test_create_deerflow_agent_live.py`
- `backend/tests/test_live_gate_markers.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-direct-entrypoint-readiness-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_requires_llm_live_entrypoint_readiness.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py::test_live_gate_marker_detector_recognizes_shared_requires_llm_helper -q
```

Result:

```text
3 failed, 1 warning in 0.37s
Failure confirmed `test_client_e2e.py` and `test_create_deerflow_agent_live.py` skipped provider-neutral live config when `OPENAI_API_KEY` was empty, and `test_create_deerflow_agent_live.py` bypassed the project model factory.

1 failed, 1 warning in 0.18s
Failure confirmed the marker guard did not recognize the shared `mark_requires_llm` helper.
```

Green focused checks:

```bash
uv --directory backend run pytest tests/test_requires_llm_live_entrypoint_readiness.py tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run pytest -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run ruff check tests/support/live_gate_readiness.py tests/test_requires_llm_live_entrypoint_readiness.py tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_live_gate_markers.py
```

Result:

```text
40 passed, 14 skipped, 1 warning in 1.66s
5 passed, 1 warning in 0.80s
34/66 tests collected (32 deselected) in 0.42s
34 skipped, 32 deselected, 1 warning in 0.44s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4844 passed, 36 skipped, 12 warnings in 85.48s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 108: Requires LLM Direct Client Live Readiness

Date: 2026-06-20

### Goal

Remove the last duplicated direct live-client readiness decision. After Batch 107, `test_client_e2e.py` and `test_create_deerflow_agent_live.py` used the shared `requires_llm` readiness helper, but `test_client_live.py` still carried its own module-level file/DB skip logic. That kept DB preload working, but left direct pytest execution and `scripts/check_stateless_live_gates.py --gate requires_llm` with separate readiness implementations.

### Steps

1. Inspect `test_client_live.py`, `test_client_live_db_mode_readiness.py`, and the shared live-gate helper.
2. Add failing tests proving `test_client_live.py` does not yet consume `support.live_gate_readiness.requires_llm_skip_reason()` or `load_active_app_config_for_requires_llm()`.
3. Change `test_client_live.py` to use the shared readiness helper and active file/DB AppConfig loader.
4. Keep the existing DB-mode direct import smoke green.
5. Tighten `test_live_gate_markers.py` so references to `requires_llm_skip_reason` do not falsely match the legacy `_requires_llm_skip` variable detector.
6. Update operator runbook, requirement audit, implementation log, and batch review.
7. Run focused checks and full backend verification.

### Files Changed

- `backend/tests/test_client_live.py`
- `backend/tests/test_client_live_db_mode_readiness.py`
- `backend/tests/test_live_gate_markers.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-client-live-shared-readiness-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py::test_client_live_entrypoint_uses_shared_requires_llm_skip_reason tests/test_client_live_db_mode_readiness.py::test_client_live_entrypoint_loads_active_config_after_shared_readiness -q
```

Result:

```text
2 failed, 1 warning in 0.22s
Failure confirmed `test_client_live.py` still produced its own local `config.yaml` skip reason and did not call the shared active-config loader.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py -q
uv --directory backend run pytest tests/test_client_live_db_mode_readiness.py tests/test_requires_llm_live_entrypoint_readiness.py tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_client_live.py tests/test_live_gate_markers.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run pytest -m requires_llm tests/test_client_e2e.py tests/test_create_deerflow_agent_live.py tests/test_deferred_tool_promotion_real_llm.py tests/test_client_live.py -q
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
uv --directory backend run ruff check tests/test_client_live.py tests/test_client_live_db_mode_readiness.py tests/test_live_gate_markers.py
```

Result:

```text
3 passed, 1 warning in 0.21s
6 passed, 1 warning in 1.19s
44 passed, 33 skipped, 1 warning in 2.12s
34/66 tests collected (32 deselected) in 0.51s
34 skipped, 32 deselected, 1 warning in 0.49s
preflight exit code 2 with config_issues=["config.yaml has no configured models"]
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4847 passed, 36 skipped, 12 warnings in 85.86s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 109: Requires LLM Project Root Preflight Parity

Date: 2026-06-20

### Goal

Close the remaining file-mode preflight mismatch for model-backed live gates. Runtime `AppConfig.resolve_config_path()` honors `DEER_FLOW_PROJECT_ROOT` through `existing_project_file(("config.yaml",))`, but `scripts/check_stateless_live_gates.py --gate requires_llm` only honored `DEER_FLOW_CONFIG_PATH` and otherwise inspected the script/default project root. Operators launching the preflight from a different working tree could therefore be falsely blocked even though runtime would load `DEER_FLOW_PROJECT_ROOT/config.yaml`.

### Steps

1. Confirm the failing behavior with a valid model config under `DEER_FLOW_PROJECT_ROOT/config.yaml` and an empty default project root.
2. Update file-mode `requires_llm` preflight so `DEER_FLOW_CONFIG_PATH` remains highest priority, then `DEER_FLOW_PROJECT_ROOT/config.yaml`, then the default project-root `config.yaml`.
3. Add invalid `DEER_FLOW_PROJECT_ROOT` tests for missing paths and non-directory paths.
4. Update operator runbook, requirement audit, implementation log, and batch review.
5. Run focused checks and full backend verification.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-requires-llm-project-root-preflight-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_deer_flow_project_root -q
```

Result:

```text
1 failed, 1 warning in 0.20s
Failure confirmed file-mode preflight ignored DEER_FLOW_PROJECT_ROOT and reported the gate as not ready.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
21 passed, 1 warning in 0.19s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4850 passed, 36 skipped, 12 warnings in 85.78s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 110: Original Plan Checklist Tracking Alignment

Date: 2026-06-20

### Goal

Close a documentation tracking gap in the original implementation plan. The plan file states that checkbox syntax is used for tracking, but after the implementation moved through many batch logs and audits, the original Task 1-8 checklist still showed almost every item as unchecked. That made the current worktree look less complete than the authoritative requirement audit and implementation log show.

### Steps

1. Re-read the original plan checklist and the current requirement audit evidence for Tasks 1-8.
2. Update `docs/harness-stateless-db-mode-plan.md` checkboxes to reflect current V1 implementation status.
3. Add explicit notes for V1 boundaries that should not be interpreted as unchecked implementation tasks:
   - public skill DB seeding and normalized skill tables are deferred scope decisions.
   - remote provisioner/K8s execution is a rollout proof gate.
4. Update requirement audit, implementation log, and batch review.
5. Verify the plan no longer contains unchecked checkboxes and run diff hygiene checks.

### Files Changed

- `docs/harness-stateless-db-mode-plan.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-plan-checklist-tracking-review.md`

### Verification

```bash
rg -n "^- \\[ \\]" docs/harness-stateless-db-mode-plan.md
git diff --check
```

Result:

```text
rg found no unchecked plan checklist items.
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute the remote provisioner/K8s live smoke in the target cluster environment.

---

## Batch 111: Live Gate Evidence Report Output

Date: 2026-06-20

### Goal

Make the remaining external live-gate proof easier to audit. The remote provisioner/K8s smoke and real model-backed `requires_llm` gate still need external environment/credential setup, but the preflight CLI only printed transient stdout. Batch 111 adds an optional JSON evidence file so operators can archive the exact preflight state and executed pytest command exit codes when those gates are run.

### Steps

1. Re-run current preflight locally to confirm external gates are still not invokable on this workstation:
   - `remote_live` is missing provisioner opt-in URL and host path/prefix env.
   - `requires_llm` reports `config.yaml has no configured models`.
2. Add failing tests for `--evidence-path`:
   - preflight-only not-ready gate writes preflight evidence and no executions.
   - ready `docker_live --run` writes executed command and exit code.
3. Implement `--evidence-path` in `scripts/check_stateless_live_gates.py`.
4. Update operator runbook, requirement audit, implementation log, and batch review.
5. Run focused checks and full backend verification.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-gate-evidence-report-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate tests/test_stateless_live_gate_check.py::test_cli_writes_run_evidence_for_ready_gate -q
```

Result:

```text
2 failed, 1 warning in 0.23s
Failure confirmed the CLI did not recognize --evidence-path.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate tests/test_stateless_live_gate_check.py::test_cli_writes_run_evidence_for_ready_gate -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.17s
23 passed, 1 warning in 0.18s
All checks passed!
```

Local gate preflight status:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --json
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json --evidence-path /tmp/deerflow-requires-llm-evidence.json
```

Result:

```text
remote_live exit code 2; missing_env included DEER_FLOW_RUN_REMOTE_AIO_SANDBOX, DEER_FLOW_REMOTE_AIO_PROVISIONER_URL, and DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH or DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX.
requires_llm exit code 2; config_issues included config.yaml has no configured models.
evidence smoke wrote selected_gates=["requires_llm"], run_requested=false, executions=[], overall_exit_code=2.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4852 passed, 36 skipped, 12 warnings in 85.81s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment.

---

## Batch 140: ACP Sandbox Native Preflight Provider Gate

Date: 2026-06-21

### Goal

Close a false-ready gap in the `acp_sandbox_native` preflight: object-runtime signing must require an AIO sandbox provider even when `acp_agents` is empty, because the executable live gate itself acquires an active leader sandbox and an isolated ephemeral ACP sandbox.

### Steps

1. Add a failing test for object runtime with `LocalSandboxProvider`, empty `acp_agents`, and `DEER_FLOW_RUN_ACP_SANDBOX_NATIVE=1`.
2. Require `sandbox.use` to include `AioSandboxProvider` whenever `acp_sandbox_native` checks object runtime config.
3. Keep the existing configured-agent checks for `execution_mode=sandbox` and `sandbox_scope=isolated`.
4. Update the operator runbook, requirement audit, and this implementation log.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_rejects_non_aio_provider_without_agents -q
```

Result:

```text
1 failed, 1 warning in 0.25s
Failure confirmed acp_sandbox_native incorrectly reported ok=True for object runtime with a non-AIO provider and no ACP agents.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_rejects_non_aio_provider_without_agents tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_accepts_isolated_sandbox_acp_in_object_runtime -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py tests/test_acp_sandbox_native_live.py -q
```

Result:

```text
2 passed, 1 warning in 0.20s
55 passed, 1 skipped, 1 warning in 0.46s
```

Full regression:

```bash
uv --directory backend run ruff check .
git diff --check
git diff --cached --check
uv --directory backend run pytest -q
```

Result:

```text
All checks passed!
git diff --check produced no output.
git diff --cached --check produced no output.
4969 passed, 37 skipped, 12 warnings in 94.28s
```

### Remaining Work

- Execute the opt-in ACP sandbox-native gate in the target environment with `DEER_FLOW_RUN_ACP_SANDBOX_NATIVE=1`.
- Execute and archive the production evidence bundle for `runtime_object_storage`, `acp_sandbox_native`, `remote_live`, and `requires_llm`, then validate it with `--require-run --require-logs`.

---

## Batch 139: ACP Sandbox Stateless Close-Out

Date: 2026-06-21

### Goal

Close the remaining strict-stateless gaps for ACP sandbox-native mode: ACP artifacts flushed by an isolated ephemeral sandbox must be visible to the active leader sandbox, object-backed ACP calls must fail closed without `thread_id`, the `acp_sandbox_native` live gate must execute a real opt-in artifact-visibility test, and provisioner create-option labels must not override reserved Pod/Service identity labels.

### Steps

1. Add failing tests for active leader ACP refresh, object-mode sandbox ACP without `thread_id`, ACP sandbox-native live-gate command/opt-in, and provisioner reserved-label protection.
2. Add `AioSandboxProvider.refresh_thread_artifacts(thread_id, roots=...)` and use it after sandbox-native ACP flush with `roots=(/mnt/acp-workspace,)`.
3. Fail closed before acquiring an ephemeral ACP sandbox when `runtime_storage.backend=object` and no `thread_id` is available.
4. Replace the self-recursive `acp_sandbox_native` gate command with `tests/test_acp_sandbox_native_live.py` and require `DEER_FLOW_RUN_ACP_SANDBOX_NATIVE=1`.
5. Filter reserved provisioner labels before writing system labels.
6. Update technical design, operator runbook, and requirement audit.

### Files Changed

- `backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_invoke_acp_agent_tool.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `backend/tests/test_provisioner_pvc_volumes.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `backend/tests/test_acp_sandbox_native_live.py`
- `backend/pyproject.toml`
- `docker/provisioner/app.py`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_mode_materializes_and_flushes_acp_workspace tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_object_mode_requires_thread_id tests/test_aio_sandbox_provider.py::test_refresh_thread_artifacts_materializes_active_sandbox_acp_root_only tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_reserved_labels_cannot_be_overridden tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_accepts_isolated_sandbox_acp_in_object_runtime tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_requires_live_opt_in -q
```

Result:

```text
6 failed, 1 warning in 0.65s
Failures confirmed missing leader refresh, missing object-mode no-thread-id guard, missing provider refresh API, reserved-label override risk, and static acp_sandbox_native gate behavior.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_mode_materializes_and_flushes_acp_workspace tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_object_mode_requires_thread_id tests/test_aio_sandbox_provider.py::test_refresh_thread_artifacts_materializes_active_sandbox_acp_root_only tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_reserved_labels_cannot_be_overridden tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_accepts_isolated_sandbox_acp_in_object_runtime tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_requires_live_opt_in -q
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox_provider.py tests/test_provisioner_pvc_volumes.py tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_acp_sandbox_native_live.py -q
```

Result:

```text
6 passed, 1 warning in 0.51s
159 passed, 1 warning in 1.37s
1 skipped, 1 warning in 0.19s
```

Full regression:

```bash
uv --directory backend run ruff check .
git diff --check
uv --directory backend run pytest -q
```

Result:

```text
All checks passed!
git diff --check produced no output.
4968 passed, 37 skipped, 12 warnings in 93.71s
```

### Remaining Work

- Execute the opt-in ACP sandbox-native gate in the target environment with `DEER_FLOW_RUN_ACP_SANDBOX_NATIVE=1`.
- Execute and archive the production evidence bundle for `runtime_object_storage`, `acp_sandbox_native`, `remote_live`, and `requires_llm`, then validate it with `--require-run --require-logs`.

---

## Batch 136: ACP Sandbox-Native Stateless Execution Foundation

Date: 2026-06-21

### Goal

Close the ACP runtime-local subprocess gap for strict stateless mode. Gateway ACP execution remains available for file-mode compatibility, but object-runtime signing now has a sandbox-native path where ACP subprocesses run in named isolated ephemeral AIO sandboxes.

### Steps

1. Add failing tests for ACP execution-mode config, sandbox ephemeral profiles, backend create options, provider `acquire_ephemeral()`, and remote provisioner option forwarding.
2. Add `SandboxCreateOptions` and propagate `name`, `image`, `labels`, and `ephemeral` through local and remote AIO backends.
3. Add `sandbox.ephemeral_profiles` and `ACPAgentConfig.execution_mode/sandbox_scope/sandbox_profile`.
4. Implement `AioSandboxProvider.acquire_ephemeral(name, profile)` with profile image/setup commands, no thread cache, no warm-pool reuse, and destroy-on-release behavior.
5. Route `invoke_acp_agent` through sandbox-native ACP execution when `execution_mode=sandbox`; bridge ACP stdio over the AIO bash session API.
6. Add the `acp_sandbox_native` live-gate preflight over file/DB app config.
7. Update technical design, operator runbook, requirement audit, and this implementation log.

### Files Changed

- `backend/packages/harness/deerflow/config/acp_config.py`
- `backend/packages/harness/deerflow/config/sandbox_config.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/backend.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/local_backend.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/remote_backend.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py`
- `backend/scripts/check_stateless_live_gates.py`
- focused tests and stateless docs

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_acp_config.py::test_acp_agent_config_defaults_to_gateway_execution_mode tests/test_acp_config.py::test_acp_agent_config_accepts_sandbox_execution_mode tests/test_aio_sandbox_provider.py::test_sandbox_config_accepts_ephemeral_profiles tests/test_aio_sandbox_provider.py::test_acquire_ephemeral_uses_named_profile_without_thread_cache tests/test_aio_sandbox_local_backend.py::test_start_container_uses_create_options_image_and_labels tests/test_remote_sandbox_backend.py::test_provisioner_create_forwards_create_options -q
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_mode_uses_ephemeral_sandbox -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_rejects_gateway_acp_in_object_runtime tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_accepts_isolated_sandbox_acp_in_object_runtime -q
```

Result:

```text
The first red check failed with missing ACP config fields, missing ephemeral profile model, missing acquire_ephemeral(), and missing SandboxCreateOptions.
The sandbox-mode ACP invocation red check failed because the tool still imported gateway spawn_agent_process.
The live-gate red check failed because acp_sandbox_native was not registered.
```

Focused green checks:

```bash
uv --directory backend run pytest tests/test_acp_config.py::test_acp_agent_config_defaults_to_gateway_execution_mode tests/test_acp_config.py::test_acp_agent_config_accepts_sandbox_execution_mode tests/test_aio_sandbox_provider.py::test_sandbox_config_accepts_ephemeral_profiles tests/test_aio_sandbox_provider.py::test_acquire_ephemeral_uses_named_profile_without_thread_cache tests/test_aio_sandbox_local_backend.py::test_start_container_uses_create_options_image_and_labels tests/test_remote_sandbox_backend.py::test_provisioner_create_forwards_create_options -q
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_mode_uses_ephemeral_sandbox -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_rejects_gateway_acp_in_object_runtime tests/test_stateless_live_gate_check.py::test_acp_sandbox_native_gate_accepts_isolated_sandbox_acp_in_object_runtime -q
```

Result:

```text
6 passed, 1 warning
1 passed, 1 warning
2 passed, 1 warning
```

### Remaining Work

- Run the broader focused suites and full regression after this batch.
- Execute `runtime_object_storage`, `acp_sandbox_native`, `remote_live`, and `requires_llm` gates in the target environment and archive validated evidence.

---

## Batch 137: Provisioner ACP Sandbox Create Options

Date: 2026-06-21

### Goal

Close the final provisioner hop for ACP sandbox-native execution. Remote backend create payloads already carry `name`, `image`, `labels`, and `ephemeral`; the bundled Kubernetes provisioner now consumes those fields when building Pods and Services, so named ACP profiles can select images without relying on gateway-local execution.

### Steps

1. Add failing provisioner tests proving create options affect the Pod manifest and are passed from the POST request model to the Pod/Service builders.
2. Add `name`, `image`, `labels`, and `ephemeral` to `CreateSandboxRequest`.
3. Apply image override to the sandbox container image.
4. Merge create labels into Pod/Service labels and add enforced `deerflow.sandbox.name` / `deerflow.sandbox.ephemeral` labels.
5. Include create options in the existing contract hash so an existing sandbox id cannot be silently reused with a different image/profile contract.

### Files Changed

- `docker/provisioner/app.py`
- `backend/tests/test_provisioner_pvc_volumes.py`
- `docs/harness-stateless-db-mode-implementation-log.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_uses_create_options_image_and_labels tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_records_create_options_in_contract_hash tests/test_provisioner_pvc_volumes.py::test_create_sandbox_passes_create_options_to_pod_builder -q
```

Result:

```text
3 failed, 1 warning
Failures confirmed _build_pod rejected name/image options and CreateSandboxRequest did not pass them through.
```

Focused green checks:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_uses_create_options_image_and_labels tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_pod_records_create_options_in_contract_hash tests/test_provisioner_pvc_volumes.py::test_create_sandbox_passes_create_options_to_pod_builder -q
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py -q
uv --directory backend run pytest tests/test_acp_config.py tests/test_aio_sandbox_provider.py tests/test_aio_sandbox_local_backend.py tests/test_remote_sandbox_backend.py tests/test_invoke_acp_agent_tool.py tests/test_stateless_live_gate_check.py tests/test_provisioner_pvc_volumes.py -q
uv --directory backend run ruff check .
uv --directory backend run pytest -q
```

Result:

```text
3 passed, 1 warning
34 passed, 1 warning
212 passed, 1 warning
All checks passed!
4962 passed, 36 skipped, 12 warnings in 93.13s
```

### Remaining Work

- Execute and archive the target-environment live gates, including `acp_sandbox_native`.

---

## Batch 138: ACP Sandbox Object Workspace Root Sync

Date: 2026-06-21

### Goal

Close the sandbox-native ACP persistence gap. Independent ACP sandboxes now materialize `/mnt/acp-workspace` from object storage before invocation and flush only that root back before release, so ACP outputs can be read by the leader sandbox later without deleting leader-owned `/mnt/user-data` artifacts.

### Steps

1. Add failing tests for root-scoped materializer flush and ACP sandbox-native object workspace materialize/flush.
2. Extend `SandboxArtifactMaterializer.materialize_thread()` and `flush_thread()` with a `roots` parameter.
3. In `invoke_acp_agent`, when `execution_mode=sandbox` and object runtime has a `thread_id`, materialize only `ACP_WORKSPACE_ROOT` into the ephemeral sandbox.
4. Flush only `ACP_WORKSPACE_ROOT` after the ACP process completes and before releasing the ephemeral sandbox.
5. Preserve `/mnt/user-data` object-store paths untouched by ACP sandbox flush.

### Files Changed

- `backend/packages/harness/deerflow/artifacts/sandbox_materializer.py`
- `backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py`
- `backend/tests/test_runtime_artifact_materializer.py`
- `backend/tests/test_invoke_acp_agent_tool.py`
- `docs/harness-stateless-db-mode-implementation-log.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_runtime_artifact_materializer.py::test_flush_thread_can_scope_roots_without_deleting_unselected_paths tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_mode_materializes_and_flushes_acp_workspace -q
```

Result:

```text
2 failed, 1 warning
Failures confirmed root-scoped flush was unsupported and ACP sandbox mode did not materialize object-store ACP workspace files.
```

Green checks:

```bash
uv --directory backend run pytest tests/test_runtime_artifact_materializer.py::test_flush_thread_can_scope_roots_without_deleting_unselected_paths tests/test_invoke_acp_agent_tool.py::test_invoke_acp_agent_sandbox_mode_materializes_and_flushes_acp_workspace -q
uv --directory backend run pytest tests/test_runtime_artifact_materializer.py tests/test_invoke_acp_agent_tool.py tests/test_aio_sandbox_provider.py tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check .
uv --directory backend run pytest -q
git diff --check
```

Result:

```text
2 passed, 1 warning
121 passed, 1 warning
All checks passed!
4964 passed, 36 skipped, 12 warnings in 92.26s
git diff --check produced no output.
```

### Remaining Work

- Execute and archive the target-environment live gates, including `runtime_object_storage`, `acp_sandbox_native`, `remote_live`, and `requires_llm`.

---

## Batch 134: Runtime Object Storage Close-Out Audit

Date: 2026-06-21

### Goal

Record the post-implementation close-out audit for the runtime PVC removal work. This batch does not change product code; it records the authoritative current state after the object-storage implementation was committed and pushed.

### Steps

1. Confirm the worktree is clean on `feature/upgrade` and the branch is aligned with `origin/feature/upgrade`.
2. Confirm the latest commit is `b1dea0c6 feat: add object storage runtime mode`.
3. Re-run full backend regression after the final async-channel object-mode fix.
4. Re-run full lint and whitespace checks.
5. Update the requirement audit with the current full-regression count and keep the live-evidence gap explicit.

### Files Changed

- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-close-out-audit-review.md`

### Verification

```bash
uv --directory backend run pytest tests/blocking_io/test_channels_ingest.py::test_ingest_inbound_files_does_not_block_event_loop -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
git status --short --branch
git ls-remote origin refs/heads/feature/upgrade
```

Result:

```text
1 passed, 1 warning in 0.70s
4936 passed, 36 skipped, 12 warnings in 91.84s
All checks passed!
git diff --check produced no output.
## feature/upgrade...origin/feature/upgrade
b1dea0c6ff26d3a99e2ace557eb25834f66ac8b8 refs/heads/feature/upgrade
```

### Remaining Work

- Execute `runtime_object_storage`, `remote_live`, and `requires_llm` gates in the target environment and archive evidence with logs.
- Validate the archived evidence bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs --json`.
- Do not mark production stateless sign-off complete until that external evidence exists.

---

## Batch 135: Multi-Gate Live Evidence Bundle Guidance

Date: 2026-06-21

### Goal

Align production sign-off guidance with the live-gate CLI's multi-gate evidence behavior. The CLI already supports repeated `--gate` arguments, so runtime-object, remote, and model gates should be archived in one evidence JSON instead of using multiple commands that target the same `<bundle>/evidence.json`.

### Steps

1. Add a regression test proving one `--evidence-path` can contain multiple selected gates, executions, and relative log paths.
2. Update the operator runbook to use repeated `--gate` arguments for production sign-off.
3. Update the requirement audit to reference the single portable evidence bundle.
4. Add a review note documenting why this avoids evidence overwrite ambiguity.

### Files Changed

- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-multi-gate-evidence-review.md`

### Verification

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_single_evidence_bundle_for_multiple_gates -q
```

Result:

```text
1 passed, 1 warning in 0.31s
```

### Remaining Work

- Execute the documented multi-gate evidence command in the target environment:
  `uv --directory backend run python scripts/check_stateless_live_gates.py --gate runtime_object_storage --gate remote_live --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs`
- Validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs --json`.

---

## Batch 113: Runtime Object Storage Foundation

Date: 2026-06-21

### Goal

Start the runtime PVC removal track by introducing the configuration and artifact-addressing foundation for object-backed runtime files. This batch does not remove the PVC yet; it creates the tested contract that later gateway, sandbox materialization, preview, and migration code will use.

### Steps

1. Add failing tests for `runtime_storage` config parsing, object-mode validation, S3-compatible defaults, runtime object key layout, path traversal rejection, and basic ArtifactStore list/delete behavior.
2. Add `RuntimeStorageConfig` and `ObjectStoreConfig`, then register `runtime_storage` on `AppConfig`.
3. Register `runtime_storage` in the startup-only reload boundary because switching filesystem/object mode requires rebuilding sandbox/provider storage wiring.
4. Add `deerflow.artifacts.store` with `ArtifactStore`, `ArtifactMetadata`, object key helpers, path validation, and an in-memory implementation for focused tests.
5. Run the focused tests for the new foundation.

### Files Changed

- `backend/packages/harness/deerflow/config/runtime_storage_config.py`
- `backend/packages/harness/deerflow/config/app_config.py`
- `backend/packages/harness/deerflow/config/__init__.py`
- `backend/packages/harness/deerflow/config/reload_boundary.py`
- `backend/packages/harness/deerflow/artifacts/__init__.py`
- `backend/packages/harness/deerflow/artifacts/store.py`
- `backend/tests/test_runtime_storage_config.py`
- `backend/tests/test_runtime_artifact_store.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-runtime-storage-foundation-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_runtime_storage_config.py tests/test_runtime_artifact_store.py -q
uv --directory backend run pytest tests/test_runtime_storage_config.py -q
```

Result:

```text
ERROR tests/test_runtime_artifact_store.py - ModuleNotFoundError: No module named 'deerflow.artifacts'
3 failed, 1 warning in 0.34s
Failures confirmed runtime_storage was not part of AppConfig and the artifact store package did not exist.
```

Focused checks:

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

### Remaining Work

- Add the S3-compatible ArtifactStore implementation and factory wiring from `RuntimeStorageConfig`.
- Wire object-backed uploads/artifacts/present-file/view-image flows.
- Add sandbox materialize/flush orchestration for `/mnt/user-data` and `/mnt/acp-workspace`.
- Add provisioner strict mode that blocks `USERDATA_PVC_NAME` when `runtime_storage.backend=object`.
- Add migration tooling from existing `.deer-flow`/runtime PVC files to object storage.

---

## Batch 114: S3-Compatible Runtime Artifact Store

Date: 2026-06-21

### Goal

Add the production object-store backend behind the Batch 113 ArtifactStore contract. The implementation targets S3-compatible APIs so the runtime PVC replacement can use open-source object storage such as SeaweedFS while preserving the `/mnt/user-data` and `/mnt/acp-workspace` path contract.

### Steps

1. Add failing tests for `S3ArtifactStore` put/get/list/delete behavior using an injected fake S3 client.
2. Add failing tests for `make_artifact_store()` selecting S3 object mode and returning `None` for filesystem compatibility mode.
3. Implement `S3ArtifactStore` with deterministic object keys, content type propagation, user metadata, and an internal `deerflow-sha256` metadata field.
4. Add lazy boto3 client construction from `ObjectStoreConfig`, including endpoint URL, region, path-style addressing, TLS verification, and env-var credentials.
5. Add `boto3` to the harness package dependencies and update `backend/uv.lock`.
6. Run focused tests, ruff, and whitespace checks.

### Files Changed

- `backend/packages/harness/deerflow/artifacts/__init__.py`
- `backend/packages/harness/deerflow/artifacts/store.py`
- `backend/packages/harness/pyproject.toml`
- `backend/uv.lock`
- `backend/tests/test_runtime_artifact_store.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-s3-artifact-store-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_runtime_artifact_store.py -q
```

Result:

```text
ERROR tests/test_runtime_artifact_store.py - ImportError: cannot import name 'S3ArtifactStore'
Failure confirmed the S3-compatible store and factory did not exist.
```

Dependency lock:

```bash
uv --directory backend lock
```

Result:

```text
Resolved 222 packages in 32.42s
Added boto3 v1.43.34
Added botocore v1.43.34
Added jmespath v1.1.0
Added s3transfer v0.19.0
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_runtime_storage_config.py tests/test_runtime_artifact_store.py tests/test_reload_boundary.py -q
uv --directory backend run ruff check packages/harness/deerflow/artifacts packages/harness/deerflow/config/runtime_storage_config.py packages/harness/deerflow/config/app_config.py packages/harness/deerflow/config/reload_boundary.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py
git diff --check
```

Result:

```text
24 passed, 1 warning in 0.23s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Wire object-backed uploads, artifact preview/download, present-file, and view-image flows into the new store.
- Add sandbox materialize/flush orchestration for `/mnt/user-data` and `/mnt/acp-workspace`.
- Add provisioner strict object mode that removes runtime PVC mounts and blocks `USERDATA_PVC_NAME`.
- Add migration tooling and live evidence for object-backed runtime files.

---

## Batch 117: Provisioner Object Runtime PVC Gate

Date: 2026-06-21

### Goal

Ensure the Kubernetes provisioner can run sandbox Pods without the runtime PVC/hostPath user-data mount when object storage is enabled. Object mode must fail closed if runtime PVC configuration is still present.

### Steps

1. Add failing tests for object mode omitting the `user-data` volume, mount, and Pod wiring.
2. Add failing tests for object mode rejecting `USERDATA_PVC_NAME`.
3. Add failing tests for object mode rejecting request-scoped extra mounts under `/mnt/user-data`.
4. Add `RUNTIME_STORAGE_BACKEND=filesystem|object` to the provisioner.
5. Implement strict validation and remove default `user-data` volume/mount in object mode.
6. Include `runtime_storage_backend` in the mount contract hash so existing filesystem Pods are not silently reused under object mode.

### Files Changed

- `docker/provisioner/app.py`
- `backend/tests/test_provisioner_pvc_volumes.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-provisioner-object-runtime-gate-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py::TestBuildVolumes::test_object_runtime_omits_userdata_volume tests/test_provisioner_pvc_volumes.py::TestBuildVolumes::test_object_runtime_rejects_userdata_pvc tests/test_provisioner_pvc_volumes.py::TestBuildVolumes::test_object_runtime_rejects_extra_userdata_mount tests/test_provisioner_pvc_volumes.py::TestBuildVolumeMounts::test_object_runtime_omits_userdata_mount tests/test_provisioner_pvc_volumes.py::TestBuildPodVolumes::test_object_runtime_pod_has_no_userdata_volume_or_mount -q
```

Result:

```text
5 failed, 1 warning in 0.58s
Failures confirmed provisioner still created user-data volume/mount and did not reject USERDATA_PVC_NAME or extra /mnt/user-data mounts.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_provisioner_pvc_volumes.py -q
uv --directory backend run ruff check ../docker/provisioner/app.py tests/test_provisioner_pvc_volumes.py
git diff --check
```

Result:

```text
31 passed, 1 warning in 0.43s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Wire `RUNTIME_STORAGE_BACKEND=object` into docker-compose/operator docs.
- Replace object-mode file locks with DB advisory locks so sandbox creation no longer creates thread directories for lock files.
- Update tool/middleware local-file readers for object mode.
- Add migration tooling and live evidence for object-backed runtime files.

---

## Batch 119: Object Runtime Sandbox Advisory Lock

Date: 2026-06-21

### Goal

Remove the last object-mode sandbox creation dependency on the runtime PVC path. Before this batch, object mode removed the sandbox mounts but sandbox creation still entered the legacy lock-file path, which called `ensure_thread_dirs()` and created local `.deer-flow` thread directories. Object mode now uses a PostgreSQL advisory lock instead.

### Steps

1. Add a failing provider test proving object-mode sandbox creation must not call `ensure_thread_dirs()`.
2. Add a failing advisory-lock test for stable lock-key generation and PostgreSQL-only factory behavior.
3. Add `sandbox_lock.py` with deterministic 63-bit advisory lock keys and a synchronous PostgreSQL advisory lock context manager.
4. Route object-mode `_discover_or_create_with_lock()` through the advisory lock and keep filesystem mode on the legacy file lock.
5. Route async object-mode creation through the same sync path in a worker thread so the lock behavior is identical.
6. Run focused sandbox/object-runtime tests, ruff, and whitespace checks.

### Files Changed

- `backend/packages/harness/deerflow/artifacts/sandbox_lock.py`
- `backend/packages/harness/deerflow/artifacts/__init__.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/tests/test_sandbox_advisory_lock.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-sandbox-advisory-lock-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_discover_or_create_object_runtime_uses_advisory_lock_without_thread_dirs -q
uv --directory backend run pytest tests/test_sandbox_advisory_lock.py -q
```

Result:

```text
FAILED tests/test_aio_sandbox_provider.py::test_discover_or_create_object_runtime_uses_advisory_lock_without_thread_dirs - AttributeError: 'AioSandboxProvider' object has no attribute '_sandbox_creation_lock'
ERROR tests/test_sandbox_advisory_lock.py - ModuleNotFoundError: No module named 'deerflow.artifacts.sandbox_lock'
Failures confirmed object-mode sandbox creation still used the legacy file-lock path and no DB advisory lock helper existed.
```

Focused checks:

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

### Remaining Work

- Wire `RUNTIME_STORAGE_BACKEND=object` and S3-compatible object-store settings into compose/operator documentation.
- Update tool/middleware local-file readers for object mode.
- Add migration tooling from `.deer-flow`/runtime PVC files to object storage.
- Add target-environment live evidence for runtime PVC removal.

---

## Batch 120: Object Runtime Entry Points

Date: 2026-06-21

### Goal

Remove object-mode runtime PVC assumptions from middleware, tools, channels, Feishu downloads, and the embedded client. These paths previously read or wrote `.deer-flow` thread directories even after sandbox mounts were removed.

### Steps

1. Add failing tests for object-mode `ThreadDataMiddleware`, `UploadsMiddleware`, and `present_files`.
2. Add failing tests for object-mode IM inbound file ingestion and outbound artifact attachment resolution.
3. Add a failing Feishu resource-download test requiring object storage writes without local upload directories or sandbox sync.
4. Add a failing `view_image` test requiring image bytes to come from `ArtifactStore`.
5. Add embedded client object-mode upload/list/delete/get_artifact tests.
6. Implement object-mode branches while preserving filesystem mode behavior.

### Files Changed

- `backend/packages/harness/deerflow/agents/middlewares/thread_data_middleware.py`
- `backend/packages/harness/deerflow/agents/middlewares/uploads_middleware.py`
- `backend/packages/harness/deerflow/tools/builtins/present_file_tool.py`
- `backend/packages/harness/deerflow/tools/builtins/view_image_tool.py`
- `backend/app/channels/manager.py`
- `backend/app/channels/feishu.py`
- `backend/packages/harness/deerflow/client.py`
- `backend/tests/test_thread_data_middleware.py`
- `backend/tests/test_uploads_middleware_core_logic.py`
- `backend/tests/test_present_file_tool_core_logic.py`
- `backend/tests/test_view_image_tool.py`
- `backend/tests/test_channels.py`
- `backend/tests/test_feishu_parser.py`
- `backend/tests/test_client.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-runtime-entry-points-review.md`

### Verification

Focused checks:

```bash
uv --directory backend run pytest tests/test_thread_data_middleware.py tests/test_uploads_middleware_core_logic.py tests/test_present_file_tool_core_logic.py -q
uv --directory backend run pytest tests/test_channels.py::TestChannelManager::test_ingest_inbound_files_object_runtime_writes_artifact_store tests/test_channels.py::TestExtractArtifacts::test_resolve_attachments_object_runtime_reads_artifact_store -q
uv --directory backend run pytest tests/test_feishu_parser.py::test_feishu_receive_single_file_object_runtime_writes_artifact_store tests/test_feishu_parser.py::test_feishu_receive_file_replaces_placeholders_in_order -q
uv --directory backend run pytest tests/test_view_image_tool.py::test_view_image_object_runtime_reads_from_artifact_store tests/test_view_image_tool.py::test_view_image_reads_virtual_uploads_path -q
uv --directory backend run pytest tests/test_client.py::TestUploads::test_upload_files tests/test_client.py::TestUploads::test_list_uploads tests/test_client.py::TestUploads::test_delete_upload tests/test_client.py::TestUploads::test_object_runtime_upload_list_delete_use_artifact_store tests/test_client.py::TestArtifacts::test_get_artifact tests/test_client.py::TestArtifacts::test_object_runtime_get_artifact_reads_artifact_store -q
uv --directory backend run ruff check packages/harness/deerflow/agents/middlewares/thread_data_middleware.py packages/harness/deerflow/agents/middlewares/uploads_middleware.py packages/harness/deerflow/tools/builtins/present_file_tool.py packages/harness/deerflow/tools/builtins/view_image_tool.py app/channels/manager.py app/channels/feishu.py packages/harness/deerflow/client.py tests/test_thread_data_middleware.py tests/test_uploads_middleware_core_logic.py tests/test_present_file_tool_core_logic.py tests/test_view_image_tool.py tests/test_channels.py tests/test_feishu_parser.py tests/test_client.py
```

Result:

```text
52 passed, 1 warning in 0.38s
2 passed, 2 warnings across channel object-mode focused tests
2 passed, 1 warning in 0.23s
2 passed, 1 warning in 0.51s
6 passed, 1 warning in 0.66s
All checks passed!
```

### Remaining Work

- Add runtime artifact migration tooling.
- Add static object-storage live-gate preflight.
- Run target-environment live evidence.

---

## Batch 121: Runtime Artifact Migration and Object Storage Gate

Date: 2026-06-21

### Goal

Make object runtime storage operable for rollout by adding a migration command for existing `.deer-flow`/PVC runtime files, adding a static object-storage gate, and documenting the deployment procedure.

### Steps

1. Add failing tests for importing user-isolated and legacy runtime artifact layouts into `ArtifactStore`.
2. Add `scripts/import_runtime_artifacts_to_object_store.py` with dry-run and apply support.
3. Add failing live-gate tests for `runtime_object_storage`.
4. Implement `runtime_object_storage` preflight over file/DB app config and provisioner env.
5. Update technical design, operator runbook, and requirement audit.

### Files Changed

- `backend/scripts/import_runtime_artifacts_to_object_store.py`
- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_import_runtime_artifacts_to_object_store.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-runtime-migration-gate-review.md`

### Verification

Focused checks:

```bash
uv --directory backend run pytest tests/test_import_runtime_artifacts_to_object_store.py -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_runtime_object_storage_gate_rejects_filesystem_runtime tests/test_stateless_live_gate_check.py::test_runtime_object_storage_gate_accepts_object_runtime -q
uv --directory backend run ruff check scripts/import_runtime_artifacts_to_object_store.py tests/test_import_runtime_artifacts_to_object_store.py scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
3 passed, 1 warning in 0.25s
2 passed, 1 warning in 0.26s
All checks passed!
```

### Remaining Work

- Execute `runtime_object_storage`, `remote_live`, and `requires_llm` gates in the target environment and archive evidence.

---

## Batch 116: Sandbox Object Materialize and Flush

Date: 2026-06-21

### Goal

Remove the sandbox runtime dependency on host/PVC mounts in object mode by materializing object-store files into the sandbox filesystem after sandbox creation/reclaim/discovery and flushing sandbox changes back to object storage before release.

### Steps

1. Add failing tests for `SandboxArtifactMaterializer` materialize and flush behavior.
2. Add a failing provider test proving object mode must not create `/mnt/user-data` or `/mnt/acp-workspace` host mounts.
3. Add failing lifecycle tests requiring sandbox creation to call materialize and release to call flush before closing the sandbox client.
4. Add a failing AIO sandbox download test for `/mnt/acp-workspace` so ACP flush is not silently skipped.
5. Implement `SandboxArtifactMaterializer`, object-mode mount suppression, provider lifecycle hooks, and ACP download permission.

### Files Changed

- `backend/packages/harness/deerflow/artifacts/sandbox_materializer.py`
- `backend/packages/harness/deerflow/artifacts/__init__.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py`
- `backend/tests/test_runtime_artifact_materializer.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `backend/tests/test_aio_sandbox.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-sandbox-materialize-flush-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox_provider.py::test_get_thread_mounts_object_runtime_skips_user_data_and_acp_mounts -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_create_sandbox_materializes_object_runtime_after_ready tests/test_aio_sandbox_provider.py::test_release_flushes_object_runtime_before_closing_sandbox -q
uv --directory backend run pytest tests/test_aio_sandbox.py::TestDownloadFile::test_allows_acp_workspace_downloads_for_runtime_flush -q
```

Result:

```text
ERROR tests/test_runtime_artifact_materializer.py - ModuleNotFoundError: No module named 'deerflow.artifacts.sandbox_materializer'
1 failed, 1 warning in 0.23s
2 failed, 1 warning in 0.23s
1 failed, 1 warning in 0.22s
Failures confirmed the materializer module, provider lifecycle hooks, and ACP download permission were missing.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_aio_sandbox.py::TestDownloadFile tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox_provider.py::test_get_thread_mounts_object_runtime_skips_user_data_and_acp_mounts tests/test_aio_sandbox_provider.py::test_create_sandbox_materializes_object_runtime_after_ready tests/test_aio_sandbox_provider.py::test_release_flushes_object_runtime_before_closing_sandbox -q
uv --directory backend run ruff check packages/harness/deerflow/artifacts packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox.py tests/test_aio_sandbox_provider.py
git diff --check
```

Result:

```text
16 passed, 1 warning in 0.27s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Add provisioner strict object mode that removes runtime PVC mounts and fails closed if `USERDATA_PVC_NAME` is set.
- Replace object-mode file locks with DB advisory locks so sandbox creation no longer requires thread directories.
- Update tool/middleware local-file readers for object mode.
- Add migration tooling and live evidence for object-backed runtime files.

---

## Batch 115: Gateway Upload and Artifact Object Mode

Date: 2026-06-21

### Goal

Route the gateway's user-facing runtime file entry/exit points through `ArtifactStore` when `runtime_storage.backend=object`. This starts removing the runtime PVC dependency from uploads and artifact preview/download while leaving filesystem mode unchanged.

### Steps

1. Add failing router tests for object-mode upload, upload listing, upload deletion, artifact text read, and missing artifact 404 behavior.
2. Add an object-mode upload branch that writes upload bytes directly to `ArtifactStore` without creating thread upload directories or acquiring a sandbox.
3. Add object-mode upload list/delete branches backed by `ArtifactStore`.
4. Add an object-mode artifact branch backed by `ArtifactStore`, including `.skill` archive byte extraction support.
5. Keep file mode as the default path and verify existing upload/artifact router tests.

### Files Changed

- `backend/app/gateway/routers/uploads.py`
- `backend/app/gateway/routers/artifacts.py`
- `backend/tests/test_uploads_router.py`
- `backend/tests/test_artifacts_router.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-object-storage-2026-06-21-gateway-object-mode-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_uploads_router.py::test_upload_files_object_mode_writes_uploads_to_artifact_store tests/test_uploads_router.py::test_list_uploaded_files_object_mode_reads_from_artifact_store tests/test_uploads_router.py::test_delete_uploaded_file_object_mode_deletes_from_artifact_store tests/test_artifacts_router.py::test_get_artifact_object_mode_reads_text_from_artifact_store tests/test_artifacts_router.py::test_get_artifact_object_mode_returns_404_for_missing_object -q
```

Result:

```text
5 failed, 2 warnings in 0.92s
Failures confirmed the uploads/artifacts routers had no make_artifact_store object-mode integration.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_uploads_router.py tests/test_artifacts_router.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py -q
uv --directory backend run ruff check app/gateway/routers/uploads.py app/gateway/routers/artifacts.py packages/harness/deerflow/artifacts tests/test_uploads_router.py tests/test_artifacts_router.py tests/test_runtime_artifact_store.py tests/test_runtime_storage_config.py
git diff --check
```

Result:

```text
63 passed, 2 warnings in 0.73s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Add sandbox materialize/flush orchestration so object-backed files are copied into `/mnt/user-data` and `/mnt/acp-workspace` before/after sandbox execution.
- Update tools that read local output paths (`present_file`, `view_image`, upload prompt middleware) to use object mode where applicable.
- Add provisioner strict object mode that removes runtime PVC mounts and blocks `USERDATA_PVC_NAME`.
- Add migration tooling and live evidence for object-backed runtime files.

---

## Batch 134: Runtime Object Storage Local-State Close-Out Fixes

Date: 2026-06-21

### Goal

Close the remaining runtime-PVC removal gaps so object mode treats object storage as the only durable source of truth for `/mnt/user-data/{workspace,uploads,outputs}` and `/mnt/acp-workspace`.

### Steps

1. Add object-store/sandbox reconcile coverage for deleted runtime files and enforce materialize file/byte budgets.
2. Flush object runtime artifacts before release, destroy, shutdown, unhealthy-drop, idle warm-pool destroy, warm-pool eviction, and shutdown warm-pool destroy paths.
3. Return `uses_thread_data_mounts=False` for local-container object mode so large tool outputs are written into sandbox paths and flushed to object storage.
4. Stage ACP workspace in a temporary directory for object mode, materialize from `/mnt/acp-workspace` before ACP invocation, and reconcile created/modified/deleted ACP files back to object storage after invocation.
5. Add object-mode upload conversion parity for gateway uploads and the embedded client.
6. Preserve filesystem mode and orphan-container warm-pool compatibility.

### Files Changed

- `backend/packages/harness/deerflow/artifacts/sandbox_materializer.py`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py`
- `backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py`
- `backend/app/gateway/routers/uploads.py`
- `backend/packages/harness/deerflow/client.py`
- `backend/tests/test_runtime_artifact_materializer.py`
- `backend/tests/test_aio_sandbox_provider.py`
- `backend/tests/test_invoke_acp_agent_tool.py`
- `backend/tests/test_uploads_router.py`
- `backend/tests/test_client.py`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_runtime_artifact_materializer.py -q
uv --directory backend run pytest tests/test_aio_sandbox_provider.py::test_destroy_flushes_object_runtime_before_closing_sandbox tests/test_aio_sandbox_provider.py::test_shutdown_flushes_object_runtime_before_destroying_active_sandboxes tests/test_aio_sandbox_provider.py::test_drop_unhealthy_sandbox_flushes_object_runtime_before_close tests/test_aio_sandbox_provider.py::test_uses_thread_data_mounts_is_false_for_local_backend_object_runtime tests/test_aio_sandbox_provider.py::test_materialize_thread_artifacts_passes_object_store_budget_config -q
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py::test_acp_workspace_context_object_runtime_materializes_and_flushes_artifact_store -q
uv --directory backend run pytest tests/test_uploads_router.py::test_upload_files_object_mode_converts_documents_to_artifact_store tests/test_client.py::TestUploads::test_object_runtime_upload_converts_documents_to_artifact_store -q
```

Result:

```text
3 failed, 2 passed, 1 warning in 0.29s
5 failed, 1 warning in 0.32s
1 failed, 1 warning in 0.35s
2 failed, 2 warnings in 0.78s
Failures confirmed the missing deletion sync, budget wiring, lifecycle flush coverage, object-mode mount routing, ACP object staging, and upload conversion parity.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_runtime_artifact_materializer.py tests/test_aio_sandbox_provider.py tests/test_tool_output_budget_middleware.py tests/test_invoke_acp_agent_tool.py -q
uv --directory backend run pytest tests/test_uploads_router.py tests/test_client.py tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_sandbox_orphan_reconciliation.py::test_reconcile_adopts_young_containers tests/test_aio_sandbox_provider.py -q
```

Result:

```text
158 passed, 1 warning in 0.76s
237 passed, 2 warnings in 1.24s
41 passed, 1 warning in 0.43s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4950 passed, 36 skipped, 12 warnings in 91.28s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute and archive `runtime_object_storage`, `remote_live`, and `requires_llm` live evidence in the target environment before production sign-off.

---

## Batch 133: Postgres Stateless Schema SQL Migration

Date: 2026-06-21

### Goal

Provide an operator-applied PostgreSQL DDL artifact for stateless DB mode instead of relying only on SQLAlchemy ORM `create_all`.

### Steps

1. Add a red regression test requiring a PG SQL migration file under `backend/packages/harness/deerflow/persistence/migrations/versions`.
2. Add `20260621_0001_stateless_db_mode_pg.sql` with tables and indexes for DB-backed config, agents, memory, MCP, and skills.
3. Use Postgres-native `JSONB`, `TIMESTAMPTZ`, identity primary keys, and plain non-unique indexes for business-key lookup.
4. Document the `psql -f` operator step in the stateless runbook.

### Follow-Up Adjustment

The Postgres SQL and ORM metadata were revised to avoid foreign keys and unique keys. Agent, memory, and skill stores now enforce business-key uniqueness in code by selecting a canonical row and pruning duplicates during write/delete paths.

### Verification

```bash
uv --directory backend run pytest tests/test_persistence_scaffold.py::TestPostgresMigrationSql::test_stateless_db_mode_pg_migration_sql_exists -q
uv --directory backend run pytest tests/test_db_agent_store.py::test_db_agent_store_save_agent_collapses_duplicate_business_keys_without_db_unique_constraint tests/test_db_memory_storage.py::test_db_memory_storage_save_collapses_duplicate_scope_without_db_unique_constraint tests/test_db_skill_storage.py::test_db_skill_storage_write_collapses_duplicate_business_keys_without_db_unique_constraints -q
```

Result:

```text
1 passed, 1 warning in 0.18s
3 passed, 1 warning in 0.31s
```

---

## Batch 132: Stateless Close-Out Verification And Live Gate Preflight

Date: 2026-06-21

### Goal

Record final local verification after the stateless close-out fixes and make the production live-gate evidence status explicit.

### Steps

1. Run the focused stateless verification suites from the repair plan.
2. Replace the missing `tests/test_skills_storage.py` path with the repository's actual `tests/test_skills_loader.py` storage/load coverage.
3. Run full backend regression, full lint, and whitespace checks after import-order cleanup.
4. Run `mcp_stateless` preflight and re-check `remote_live` / `requires_llm` readiness.
5. Add a final close-out review document for the remaining production sign-off risk.

### Verification

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skills_loader.py tests/test_sandbox_materializer.py -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run pytest tests/test_memory_prompt_injection.py tests/test_memory_updater.py -q
uv --directory backend run pytest tests/test_mcp_db_store.py tests/test_mcp_cache_revision.py tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
31 passed, 1 warning in 0.59s
28 passed, 1 warning in 2.06s
70 passed, 1 warning in 0.42s
53 passed, 1 warning in 0.83s
4882 passed, 36 skipped, 12 warnings in 87.16s
All checks passed!
git diff --check completed with no output.
```

Live-gate preflight:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate mcp_stateless --json
uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --json
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
```

Result:

```text
mcp_stateless exit code 0; ready_to_invoke=true.
remote_live exit code 2; missing_env included DEER_FLOW_RUN_REMOTE_AIO_SANDBOX, DEER_FLOW_REMOTE_AIO_PROVISIONER_URL, and DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH or DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX.
requires_llm exit code 2; config_issues included config.yaml has no configured models.
```

### Remaining Work

- Execute and archive `remote_live` evidence in the target provisioner/K8s environment.
- Execute and archive `requires_llm` evidence in an environment with valid model configuration and provider credentials.

---

## Batch 131: MCP Strict Stateless Compatibility Gate

Date: 2026-06-21

### Goal

Prevent production sign-off from mistaking DB-backed MCP configuration for fully stateless MCP runtime behavior.

### Steps

1. Add failing tests for `mcp_stateless` rejecting untagged enabled stdio MCP, accepting HTTP/SSE and explicitly tagged stdio MCP, and checking DB-backed MCP rows.
2. Add `mcp_stateless` to `scripts/check_stateless_live_gates.py`.
3. Add compatibility reports for HTTP/SSE, disabled stdio, tagged stdio, and blocked stdio.
4. Add the same `extensions.mcp_compatibility` shape to runtime-state import dry-run output.
5. Update operator runbook, requirement audit, implementation log, and review docs.

### Verification

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_mcp_stateless_gate_rejects_enabled_stdio_without_runtime_mode tests/test_stateless_live_gate_check.py::test_mcp_stateless_gate_accepts_http_and_explicitly_tagged_stdio tests/test_stateless_live_gate_check.py::test_mcp_stateless_gate_checks_db_mcp_config -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_public_and_custom_skills tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_config_extensions_and_mcp -q
```

Result:

```text
3 passed, 1 warning in 0.22s
3 passed, 1 warning in 0.39s
```

## Batch 130: Memory Updater Budgeted Existing-Memory Snapshot

Date: 2026-06-21

### Goal

Prevent Memory updater prompts from growing with the full persisted memory JSON while keeping DB memory complete and hierarchical.

### Steps

1. Add failing tests for `MemoryConfig.max_update_context_tokens` and bounded updater prompt snapshots.
2. Add `memory.max_update_context_tokens` with default `12000`.
3. Add updater-specific memory formatting that keeps hierarchical summaries and top-ranked facts within budget.
4. Switch `MemoryUpdater._prepare_update_prompt()` to use the budgeted snapshot while returning the original memory object for persistence.
5. Add review documentation.

### Verification

```bash
uv --directory backend run pytest tests/test_memory_updater.py::test_memory_config_has_update_context_budget_default tests/test_memory_updater.py::test_prepare_update_prompt_uses_budgeted_memory_snapshot_without_trimming_persisted_state -q
uv --directory backend run pytest tests/test_memory_prompt_injection.py -q
```

Result:

```text
2 passed, 1 warning in 0.25s
11 passed, 1 warning in 0.30s
```

## Batch 129: Normalized Public And Custom Skill DB Storage

Date: 2026-06-21

### Goal

Close the stateless skill storage gap by making DB mode read both public and custom skills from normalized DB tables instead of relying on gateway public-skill filesystem artifacts.

### Steps

1. Add failing tests for public skill DB seed/read, public-skill fail-closed behavior, and legacy custom backfill into normalized rows.
2. Add `skills` and `skill_files` ORM rows while keeping `custom_skills` as a compatibility/backfill table.
3. Change `DbSkillStorage` list/read/manifest paths to use normalized rows for public and custom skills.
4. Add public skill seed/import APIs and update runtime-state migration to import public and custom skills into DB.
5. Update runbook, requirement audit, V1 scope decisions, implementation log, and review docs.

### Verification

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py::test_db_skill_storage_seeds_public_skill_from_db_without_filesystem tests/test_db_skill_storage.py::test_db_skill_storage_public_skill_missing_seed_fails_closed tests/test_db_skill_storage.py::test_db_skill_storage_backfills_legacy_custom_rows_to_normalized_tables -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_public_and_custom_skills tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_config_extensions_and_mcp -q
```

Result:

```text
3 passed, 1 warning in 0.20s
3 passed, 1 warning in 0.39s
```

### Remaining Work

- Execute and archive `remote_live` evidence in the target provisioner/K8s environment.
- Execute and archive `requires_llm` evidence in an environment with valid model configuration and provider credentials.

---

## Batch 128: Live Gate Evidence Gate List Uniqueness

Date: 2026-06-21

### Goal

Tighten strict rollout evidence so gate lists themselves are unambiguous. Before this batch, `--validate-evidence --require-run` could accept hand-authored evidence with duplicate `selected_gates`, or duplicate selected entries inside `preflight.gates`. Generated evidence never emits duplicate selected/preflight gate entries, so strict validation now rejects these shapes.

### Steps

1. Add failing strict-validation tests for:
   - duplicate gate names in `selected_gates`;
   - duplicate selected gate entries in `preflight.gates`.
2. Add a reusable duplicate-string helper for strict evidence validation.
3. Use it to reject duplicate selected gate names.
4. Extend selected preflight gate validation to reject duplicate selected gate entries.
5. Run focused tests, focused lint, full backend regression, full lint, and update the operator runbook, requirement audit, implementation log, and consolidated live-gate evidence hardening review.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-hardening-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_duplicate_selected_gate tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_duplicate_selected_preflight_gate -q
```

Result:

```text
2 failed, 1 warning in 0.21s
Failure confirmed --require-run accepted duplicate selected/preflight gate entries.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_duplicate_selected_gate tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_duplicate_selected_preflight_gate -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.16s
45 passed, 1 warning in 0.26s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4874 passed, 36 skipped, 12 warnings in 89.52s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in the target cluster environment, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.

---

## Batch 127: Live Gate Evidence Execution Set Strictness

Date: 2026-06-21

### Goal

Tighten strict rollout evidence so execution records have a one-to-one relationship with selected gates. Before this batch, `--validate-evidence --require-run` could accept archived evidence that selected `docker_live` but also included an extra unselected `remote_live` execution, or evidence that duplicated the selected gate execution record. The generator never emits those shapes, so strict validation now rejects them as evidence tampering or ambiguity.

### Steps

1. Add failing strict-validation tests for:
   - an execution record whose `gate` is not present in `selected_gates`;
   - duplicate execution records for the same selected gate.
2. Extend `--require-run` validation to reject:
   - execution gates that were not selected;
   - duplicate execution gate names.
3. Preserve the existing missing-gate, command-match, per-gate readiness, exit-code consistency, and log-integrity validation behavior.
4. Run focused tests, focused lint, full backend regression, full lint, and update the operator runbook, requirement audit, implementation log, and consolidated live-gate evidence hardening review.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-hardening-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_unselected_execution_gate tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_duplicate_execution_gate -q
```

Result:

```text
2 failed, 1 warning in 0.23s
Failure confirmed --require-run accepted unselected and duplicate execution records.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_unselected_execution_gate tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_duplicate_execution_gate -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.18s
43 passed, 1 warning in 0.30s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4872 passed, 36 skipped, 12 warnings in 89.51s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in the target cluster environment, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.

---

## Batch 126: Live Gate Evidence Selected Gate Strictness

Date: 2026-06-21

### Goal

Close a strict evidence-signoff loophole left after Batch 125. Before this batch, `--validate-evidence --require-run` could accept hand-authored evidence that had no selected gates, a selected gate name outside the supported live-gate registry, a not-ready top-level preflight, or a selected gate whose `ready_to_invoke` state was false while execution records were forged as successful. Batch 126 requires strict run evidence to select at least one known gate and to record ready preflight state before execution coverage can satisfy rollout sign-off.

### Steps

1. Add failing strict-validation tests for:
   - empty `selected_gates` with `run_requested: true` and `overall_exit_code: 0`;
   - unknown selected gate names with matching forged preflight/execution records;
   - `preflight.ok: false` paired with a successful execution record;
   - selected gate `ready_to_invoke: false` paired with a successful execution record.
2. Extend `--require-run` validation to reject:
   - empty selected-gate lists;
   - selected gates not present in the current `GATE_SPECS` registry;
   - non-ready top-level preflight evidence;
   - selected preflight gates whose `ready_to_invoke` state is not true.
3. Run focused tests, focused lint, full backend regression, full lint, and update the operator runbook, requirement audit, implementation log, and consolidated live-gate evidence hardening review.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-hardening-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_empty_selected_gates tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_unknown_selected_gate tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_not_ready_preflight -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_not_ready_selected_gate -q
```

Result:

```text
3 failed, 1 warning in 0.23s
Failure confirmed --require-run accepted empty selected gates, unknown selected gates, and not-ready preflight evidence.

1 failed, 1 warning in 0.23s
Failure confirmed --require-run accepted a selected gate whose ready_to_invoke state was false.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_empty_selected_gates tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_unknown_selected_gate tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_not_ready_preflight -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_not_ready_selected_gate -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
3 passed, 1 warning in 0.18s
1 passed, 1 warning in 0.19s
41 passed, 1 warning in 0.30s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4870 passed, 36 skipped, 12 warnings in 90.98s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in the target cluster environment, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.

---

## Batch 125: Live Gate Evidence Log Integrity Metadata

Date: 2026-06-21

### Goal

Close the remaining evidence-log integrity gap. Before this batch, `--require-logs` proved referenced stdout/stderr log files existed, but it did not prove those files still matched the logs written when evidence was generated. Batch 125 records SHA-256 and byte counts for captured logs and makes strict log validation reject missing or mismatched integrity metadata.

### Steps

1. Add a failing generation test asserting `--evidence-log-dir` records:
   - `stdout_log_sha256`
   - `stdout_log_bytes`
   - `stderr_log_sha256`
   - `stderr_log_bytes`
2. Write log files from UTF-8 bytes and record SHA-256/byte counts alongside log paths.
3. Add a failing strict-validation test whose evidence records original log hashes but whose archived stdout file has been tampered with.
4. Extend `--require-logs` validation to require and verify SHA-256/byte-count metadata for every referenced stdout/stderr log file.
5. Update hand-authored strict log evidence fixtures to include matching integrity metadata.
6. Update operator runbook, requirement audit, implementation log, and consolidated live-gate evidence hardening review.
7. Run focused tests, focused lint, full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-hardening-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_execution_logs_for_run_evidence -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_logs_rejects_tampered_execution_log -q
```

Result:

```text
1 failed, 1 warning in 0.25s
Failure confirmed generated evidence did not include stdout/stderr log integrity metadata.

1 failed, 1 warning in 0.23s
Failure confirmed --require-logs accepted a tampered archived stdout log.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_execution_logs_for_run_evidence -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_logs_rejects_tampered_execution_log tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_logs_resolves_relative_paths_from_evidence_dir -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.21s
2 passed, 1 warning in 0.19s
37 passed, 1 warning in 0.32s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4866 passed, 36 skipped, 12 warnings in 89.57s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in the target cluster environment, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.

---

## Batch 124: Live Gate Evidence Execution Command Matching

Date: 2026-06-21

### Goal

Tighten rollout sign-off evidence so `--require-run` proves the selected gate command actually ran, not only that an execution record reused the selected gate name. Before this batch, archived evidence with `selected_gates: ["docker_live"]`, `run_requested: true`, `overall_exit_code: 0`, and an execution record for `docker_live` could validate even if the execution command was not the preflight command for that gate.

### Steps

1. Add a failing CLI validation test where:
   - preflight records the expected `docker_live` command.
   - execution records the same gate name but a different command.
   - `overall_exit_code` is `0`.
   - validation uses `--validate-evidence --require-run`.
2. Extend strict run validation to read selected gate commands from `preflight.gates`.
3. Reject strict evidence when:
   - `preflight.gates` is missing or malformed.
   - a selected gate has no string-list preflight command.
   - an execution command is missing/malformed.
   - an execution command differs from the preflight command for that gate.
4. Update hand-authored strict evidence fixtures to include preflight commands matching generated evidence.
5. Update operator runbook, requirement audit, implementation log, and consolidated live-gate evidence hardening review.
6. Run focused tests, focused lint, full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-hardening-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_mismatched_execution_command -q
```

Result:

```text
1 failed, 1 warning in 0.22s
Failure confirmed --require-run accepted an execution record whose command did not match the preflight command.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_mismatched_execution_command -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.20s
36 passed, 1 warning in 0.30s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4865 passed, 36 skipped, 12 warnings in 90.16s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in the target cluster environment, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.

---

## Batch 123: Live Gate Portable Evidence Bundle Generation

Date: 2026-06-21

### Goal

Make live-gate evidence generation match the portable bundle semantics added to validation in Batch 122. Validation could already resolve relative log paths from the evidence JSON directory, but generation still wrote a relative `--evidence-log-dir` from the process cwd. Batch 123 makes `--evidence-path bundle/evidence.json --evidence-log-dir logs` write logs to `bundle/logs/` and record `logs/...` in the JSON, so the generated bundle can be archived or moved as a unit.

### Steps

1. Add a failing CLI test that:
   - writes evidence under a bundle directory.
   - invokes `--run --evidence-log-dir logs` from a different cwd.
   - expects stdout/stderr logs under the evidence bundle.
   - expects execution records to keep relative `logs/...` paths.
   - validates the generated evidence with `--validate-evidence --require-run --require-logs`.
2. Split execution-log handling into physical write path and recorded evidence path prefix.
3. Resolve relative `--evidence-log-dir` against the evidence file parent when `--evidence-path` is present.
4. Preserve absolute `--evidence-log-dir` behavior for existing callers.
5. Update operator runbook, requirement audit, implementation log, and the consolidated live-gate evidence hardening review.
6. Run focused tests, focused lint, full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-hardening-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_relative_execution_logs_next_to_evidence_bundle -q
```

Result:

```text
1 failed, 1 warning in 0.23s
Failure confirmed relative --evidence-log-dir was written from the process cwd instead of the evidence bundle directory.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_relative_execution_logs_next_to_evidence_bundle -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.18s
35 passed, 1 warning in 0.30s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4864 passed, 36 skipped, 12 warnings in 90.82s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <bundle>/evidence.json --evidence-log-dir logs` in the target cluster environment, then validate the archived bundle with `--validate-evidence <bundle>/evidence.json --require-run --require-logs`.

---

## Batch 122: Live Gate Evidence Relative Log Path Validation

Date: 2026-06-21

### Goal

Make archived live-gate evidence bundles more portable by resolving relative execution log paths from the evidence file directory. Batch 121 added `--require-logs`, but relative `stdout_log_path` / `stderr_log_path` values were checked against the validator's current working directory. Batch 122 lets operators move or unpack an evidence bundle and validate paths such as `logs/001-remote_live.stdout.log` relative to the JSON evidence file.

### Steps

1. Add a failing test with:
   - an evidence file under a bundle directory.
   - stdout/stderr logs under `bundle/logs/`.
   - relative log paths in the execution record.
   - validation invoked from the normal test cwd.
2. Resolve non-absolute execution log paths relative to the evidence file parent directory during `--require-logs` validation.
3. Keep absolute log path validation unchanged.
4. Update operator runbook, requirement audit, implementation log, and batch review.
5. Run focused tests, full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-relative-log-path-review.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-hardening-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_logs_resolves_relative_paths_from_evidence_dir -q
```

Result:

```text
1 failed, 1 warning in 0.21s
Failure confirmed relative log paths were resolved from cwd instead of the evidence directory.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_logs_resolves_relative_paths_from_evidence_dir -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.18s
34 passed, 1 warning in 0.27s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4863 passed, 36 skipped, 12 warnings in 89.60s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file> --evidence-log-dir <dir>` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived evidence with `--validate-evidence --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file> --evidence-log-dir <dir>` in the target cluster environment, then validate the archived evidence with `--validate-evidence --require-run --require-logs`.

---

## Batch 121: Live Gate Evidence Required Log Validation

Date: 2026-06-21

### Goal

Make archived live-gate sign-off validate that stdout/stderr logs referenced by the evidence were actually preserved. Batch 120 records `stdout_log_path` and `stderr_log_path` when operators pass `--evidence-log-dir`, but validation still could not reject evidence whose execution records had no archived logs. Batch 121 adds `--require-logs` for strict rollout validation.

### Steps

1. Add a failing test for `--validate-evidence --require-logs` using successful execution evidence without `stdout_log_path` or `stderr_log_path`.
2. Add `--require-logs` CLI validation mode.
3. Validate that each execution contains string `stdout_log_path` and `stderr_log_path` fields when strict log validation is enabled.
4. Validate that referenced stdout/stderr log files exist.
5. Update operator runbook, requirement audit, implementation log, and batch review.
6. Run focused tests, full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-required-log-validation-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_logs_rejects_missing_execution_log_paths -q
```

Result:

```text
1 failed, 1 warning in 0.23s
Failure confirmed --require-logs was not recognized by the CLI.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_logs_rejects_missing_execution_log_paths -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.19s
33 passed, 1 warning in 0.30s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4862 passed, 36 skipped, 12 warnings in 89.95s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file> --evidence-log-dir <dir>` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived evidence with `--validate-evidence --require-run --require-logs`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file> --evidence-log-dir <dir>` in the target cluster environment, then validate the archived evidence with `--validate-evidence --require-run --require-logs`.

---

## Batch 120: Live Gate Evidence Execution Log Capture

Date: 2026-06-21

### Goal

Make external live-gate sign-off evidence more self-contained by letting operators archive each executed pytest command's stdout/stderr beside the JSON evidence file. Earlier evidence batches recorded commands and exit codes, but troubleshooting still depended on separately preserving terminal output. Batch 120 adds `--evidence-log-dir` so rollout records can point directly to per-gate logs.

### Steps

1. Add a failing CLI test for `--run --evidence-path --evidence-log-dir` with a ready `docker_live` gate and a fake runner that returns stdout/stderr.
2. Add `--evidence-log-dir` to the live-gate preflight CLI.
3. When running gates with an evidence log directory, write `001-<gate>.stdout.log` and `001-<gate>.stderr.log` files for captured command output.
4. Record `stdout_log_path` and `stderr_log_path` on each execution entry when logs are captured.
5. Preserve existing runner behavior when no log directory is requested.
6. Update operator runbook, requirement audit, implementation log, and batch review.
7. Run focused tests, full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-log-capture-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_execution_logs_for_run_evidence -q
```

Result:

```text
1 failed, 1 warning in 0.21s
Failure confirmed --evidence-log-dir was not recognized by the CLI.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_execution_logs_for_run_evidence -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.22s
32 passed, 1 warning in 0.31s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4861 passed, 36 skipped, 12 warnings in 89.45s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file> --evidence-log-dir <dir>` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived evidence with `--validate-evidence --require-run`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file> --evidence-log-dir <dir>` in the target cluster environment, then validate the archived evidence with `--validate-evidence --require-run`.

---

## Batch 119: Live Gate Evidence Execution Exit-Code Consistency

Date: 2026-06-21

### Goal

Close the remaining strict evidence-validation gap where an archived rollout record could claim `overall_exit_code: 0` while an individual gate execution recorded a non-zero `exit_code`. Batch 119 makes evidence validation reject that contradictory success record, so remote/model sign-off cannot rely on a root outcome that disagrees with the captured per-command exit codes.

### Steps

1. Add a failing test for `--validate-evidence --require-run` with:
   - `run_requested: true`.
   - a selected `remote_live` execution record.
   - execution `exit_code: 2`.
   - root `overall_exit_code: 0`.
2. Add execution exit-code validation to archived evidence parsing.
3. Reject `overall_exit_code: 0` when any recorded execution has a non-zero `exit_code`.
4. Verify the focused failing test, the full live-gate preflight suite, and targeted lint.
5. Update operator runbook, requirement audit, implementation log, and batch review.
6. Run full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-exit-code-consistency-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_success_with_failed_execution -q
```

Result:

```text
1 failed, 1 warning in 0.21s
Failure confirmed validation returned exit code 0 for contradictory evidence.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_success_with_failed_execution -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
1 passed, 1 warning in 0.18s
31 passed, 1 warning in 0.24s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4860 passed, 36 skipped, 12 warnings in 88.70s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived evidence with `--validate-evidence --require-run`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment, then validate the archived evidence with `--validate-evidence --require-run`.

---

## Batch 118: Live Gate Evidence Require-Run Validation

Date: 2026-06-21

### Goal

Prevent preflight-only evidence from being mistaken for completed remote/model gate sign-off. Batch 117 added evidence validation, but an evidence file with `overall_exit_code: 0` and `run_requested: false` could still validate as successful. Batch 118 adds `--require-run` so rollout sign-off can require that `--run` was requested and every selected gate has an execution record.

### Steps

1. Add failing tests for `--validate-evidence --require-run`:
   - accepted when evidence records an executed selected gate with exit code `0`.
   - rejected when evidence is preflight-only even if `overall_exit_code` is `0`.
2. Add `--require-run` CLI flag and pass it to evidence validation.
3. Validate `run_requested` and execution coverage for all selected gates when strict mode is enabled.
4. Verify focused tests, full live-gate preflight suite, targeted lint, and a manual strict-validation smoke.
5. Update operator runbook, requirement audit, implementation log, and batch review.
6. Run full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-require-run-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_accepts_executed_gate_evidence tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_preflight_only_success -q
```

Result:

```text
2 failed, 1 warning in 0.27s
Failure confirmed --require-run was not recognized by the CLI.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_accepts_executed_gate_evidence tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_require_run_rejects_preflight_only_success -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.17s
30 passed, 1 warning in 0.23s
All checks passed!
```

Manual strict-validation smoke:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json --evidence-path /tmp/deerflow-requires-llm-require-run-evidence.json
uv --directory backend run python scripts/check_stateless_live_gates.py --validate-evidence /tmp/deerflow-requires-llm-require-run-evidence.json --require-run --json
```

Result:

```text
requires_llm preflight evidence exit code 2 because local config.yaml has no configured models.
strict validate-evidence returned exit code 1 with valid=false, success=false, and errors for run_requested plus missing selected-gate execution.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4859 passed, 36 skipped, 12 warnings in 85.80s
All checks passed!
git diff --check completed with no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived evidence with `--validate-evidence --require-run`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment, then validate the archived evidence with `--validate-evidence --require-run`.

---

## Batch 117: Live Gate Evidence Validation CLI

Date: 2026-06-21

### Goal

Make archived remote/model live-gate evidence machine-checkable after it is produced. Before this batch, operators could generate JSON evidence but had no script-level way to distinguish a malformed evidence file from a valid evidence file that recorded a failed gate. Batch 117 adds `--validate-evidence` to validate evidence shape and return the archived gate outcome.

### Steps

1. Add failing tests for:
   - valid successful evidence returns exit code `0`.
   - valid failed evidence returns exit code `2`.
   - missing `schema_version` returns exit code `1`.
   - boolean `schema_version` is rejected instead of being treated as integer `1`.
2. Add evidence JSON validation helpers for root schema fields and archived outcome extraction.
3. Add `--validate-evidence <path>` CLI mode with JSON and text output.
4. Verify focused red/green tests, full live-gate preflight suite, targeted lint, and manual validation of generated evidence.
5. Update operator runbook, requirement audit, implementation log, and batch review.
6. Run full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-21-live-gate-evidence-validation-cli-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validates_successful_evidence_file tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_returns_gate_status_for_failed_evidence tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_rejects_missing_schema_version -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_rejects_boolean_schema_version -q
```

Result:

```text
3 failed, 1 warning in 0.33s
1 failed, 1 warning in 0.22s
Failures confirmed --validate-evidence was absent and boolean schema_version was incorrectly accepted.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_validates_successful_evidence_file tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_returns_gate_status_for_failed_evidence tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_rejects_missing_schema_version tests/test_stateless_live_gate_check.py::test_cli_validate_evidence_rejects_boolean_schema_version -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
4 passed, 1 warning in 0.20s
28 passed, 1 warning in 0.28s
All checks passed!
```

Manual evidence validation smoke:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json --evidence-path /tmp/deerflow-requires-llm-validate-evidence.json
uv --directory backend run python scripts/check_stateless_live_gates.py --validate-evidence /tmp/deerflow-requires-llm-validate-evidence.json --json
```

Result:

```text
requires_llm preflight evidence exit code 2 because local config.yaml has no configured models.
validate-evidence returned exit code 2 with valid=true, success=false, schema_version=1, selected_gates=["requires_llm"], and overall_exit_code=2.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4857 passed, 36 skipped, 12 warnings in 91.24s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials, then validate the archived evidence with `--validate-evidence`.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment, then validate the archived evidence with `--validate-evidence`.

---

## Batch 116: Live Gate Checked Environment Evidence

Date: 2026-06-20

### Goal

Make live-gate evidence more auditable without storing secrets. Before this batch, a ready gate only showed empty `missing_env`, so archived evidence did not show which environment variable names were actually checked. Batch 116 adds `checked_env` to each gate report with variable names only, never values.

### Steps

1. Add failing tests requiring `checked_env` on:
   - a ready `remote_live` preflight with provisioner and host-path-prefix variables.
   - a ready file-mode `requires_llm` preflight with a model credential env reference.
   - DB-mode `requires_llm` preflight when `DEER_FLOW_DATABASE_URL` is missing.
   - DB-mode `requires_llm` preflight when the DB exists but `runtime_configs.app` is missing.
2. Extend `ConfigReadiness` and gate report generation to carry checked env names.
3. Include required env names, one-of env names, DB URL checks, and model `$ENV` references in `checked_env`.
4. Verify focused red/green tests, full live-gate preflight suite, targeted lint, and a manual evidence smoke.
5. Update operator runbook, requirement audit, implementation log, and batch review.
6. Run full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-gate-checked-env-evidence-review.md`

### Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_remote_live_gate_accepts_host_path_prefix tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_config_with_models -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_db_mode_requires_database_url -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_requires_llm_gate_db_mode_reports_checked_env_when_app_config_missing -q
```

Result:

```text
2 failed, 1 warning in 0.22s
1 failed, 1 warning in 0.22s
1 failed, 1 warning in 0.23s
Failures confirmed checked_env was absent or incomplete.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_remote_live_gate_accepts_host_path_prefix tests/test_stateless_live_gate_check.py::test_requires_llm_gate_accepts_config_with_models tests/test_stateless_live_gate_check.py::test_requires_llm_gate_db_mode_requires_database_url tests/test_stateless_live_gate_check.py::test_requires_llm_gate_db_mode_reports_checked_env_when_app_config_missing -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
4 passed, 1 warning in 0.20s
24 passed, 1 warning in 0.30s
All checks passed!
```

Manual evidence smoke:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --json --evidence-path /tmp/deerflow-remote-live-checked-env-evidence.json
rg -n '"checked_env"|DEER_FLOW_REMOTE_AIO|"schema_version"|"overall_exit_code"' /tmp/deerflow-remote-live-checked-env-evidence.json
```

Result:

```text
remote_live exit code 2; missing_env reported the expected remote-live prerequisites.
evidence smoke wrote checked_env names for DEER_FLOW_REMOTE_AIO_PROVISIONER_URL, DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH, DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX, and DEER_FLOW_RUN_REMOTE_AIO_SANDBOX.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4853 passed, 36 skipped, 12 warnings in 90.69s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment.

---

## Batch 115: Live Gate Evidence Schema Version

Date: 2026-06-20

### Goal

Make live-gate evidence files easier to parse and preserve across future format changes by adding an explicit root-level `schema_version` field. This keeps remote/model rollout evidence machine-readable as the JSON report evolves.

### Steps

1. Add failing evidence tests requiring `schema_version: 1` for both preflight-only and `--run` evidence reports.
2. Add `schema_version: 1` to the root evidence JSON emitted by `scripts/check_stateless_live_gates.py`.
3. Run focused evidence tests, the full live-gate preflight suite, targeted lint, and a local CLI evidence smoke.
4. Update operator runbook, requirement audit, implementation log, and batch review.
5. Run full backend regression, full lint, and whitespace diff checks.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-gate-evidence-schema-version-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate -q
```

Result:

```text
1 failed, 1 warning in 0.19s
Failure confirmed evidence JSON did not include schema_version.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate tests/test_stateless_live_gate_check.py::test_cli_writes_run_evidence_for_ready_gate -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.20s
23 passed, 1 warning in 0.26s
All checks passed!
```

Manual evidence smoke:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json --evidence-path /tmp/deerflow-requires-llm-schema-evidence.json
rg -n '"schema_version"|"selected_gates"|"overall_exit_code"|"config.yaml has no configured models"' /tmp/deerflow-requires-llm-schema-evidence.json
```

Result:

```text
requires_llm exit code 2; config_issues included config.yaml has no configured models.
evidence smoke wrote schema_version=1, selected_gates=["requires_llm"], run_requested=false, executions=[], overall_exit_code=2.
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4852 passed, 36 skipped, 12 warnings in 86.95s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment.

---

## Batch 114: Live Gate Source Metadata Test Portability

Date: 2026-06-20

### Goal

Remove an unnecessary local-git-worktree assumption from the live-gate evidence source metadata test. Batch 113 made the evidence file include best-effort git source metadata, but the focused test asserted against the real checkout. That was fine in this repo but fragile for CI/source-package runs without `.git`.

### Steps

1. Review the Batch 113 evidence metadata test and identify the real `.git` dependency.
2. Inject deterministic `_git_metadata()` output in the evidence test via `monkeypatch`.
3. Assert the evidence report preserves the injected `source.git` payload exactly.
4. Run focused preflight tests, targeted lint, full backend regression, and whitespace diff checks.

### Files Changed

- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-gate-source-metadata-test-portability-review.md`

### Verification

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
23 passed, 1 warning in 0.24s
All checks passed!
4852 passed, 36 skipped, 12 warnings in 86.58s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment.

---

## Batch 113: Live Gate Evidence Source Metadata

Date: 2026-06-20

### Goal

Make live-gate evidence files traceable to the source checkout used for the run. Batch 111 added the evidence file and Batch 112 added time/cwd metadata; Batch 113 adds git source metadata when git is available.

### Steps

1. Add a failing test requiring `source.git` metadata in the evidence JSON.
2. Implement git source metadata with graceful fallback when git is unavailable or the command is not run inside a repository.
3. Update operator runbook, requirement audit, implementation log, and batch review.
4. Run focused checks and full backend verification.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-gate-evidence-source-metadata-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate -q
```

Result:

```text
1 failed, 1 warning in 0.19s
Failure confirmed evidence JSON did not include source metadata.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate tests/test_stateless_live_gate_check.py::test_cli_writes_run_evidence_for_ready_gate -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.23s
23 passed, 1 warning in 0.24s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4852 passed, 36 skipped, 12 warnings in 85.80s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment.

---

## Batch 112: Live Gate Evidence Metadata

Date: 2026-06-20

### Goal

Improve the live-gate evidence file added in Batch 111 so archived external-gate records include basic provenance. The evidence JSON now records the generation timestamp and process working directory in addition to preflight state and command exit codes.

### Steps

1. Add a failing test requiring `generated_at_utc` and `cwd` in the evidence JSON.
2. Implement the two metadata fields in `scripts/check_stateless_live_gates.py`.
3. Update operator runbook, requirement audit, implementation log, and batch review.
4. Run focused checks and full backend verification.

### Files Changed

- `backend/scripts/check_stateless_live_gates.py`
- `backend/tests/test_stateless_live_gate_check.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/reviews/harness-stateless-db-mode-2026-06-20-live-gate-evidence-metadata-review.md`

### Verification

Red check:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate -q
```

Result:

```text
1 failed, 1 warning in 0.19s
Failure confirmed evidence JSON did not include generated_at_utc.
```

Focused checks:

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py::test_cli_writes_preflight_evidence_for_not_ready_gate tests/test_stateless_live_gate_check.py::test_cli_writes_run_evidence_for_ready_gate -q
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
```

Result:

```text
2 passed, 1 warning in 0.16s
23 passed, 1 warning in 0.18s
All checks passed!
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4852 passed, 36 skipped, 12 warnings in 85.92s
All checks passed!
git diff --check produced no output.
```

### Remaining Work

- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path <file>` against a real file-mode or DB-mode model configuration with valid provider credentials.
- Execute `uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run --evidence-path <file>` in the target cluster environment.
