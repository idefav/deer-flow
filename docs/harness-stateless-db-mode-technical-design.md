# Harness Stateless DB-backed Runtime State Technical Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Harness 运行态从本地文件依赖改为 DB-backed stateless runtime，并在每次运行时把必要的
config、agent、memory、skills 信息下发到 AIOSandbox。

**Architecture:** 保留现有 Harness 与 App/Gateway 边界，在 Harness 内新增 source/store 抽象和 DB 实现。
第一阶段保持行为兼容，第二阶段启用 sandbox materialization，第三阶段再做 memory query recall、
MCP sidecar、secret store 等增强。

**Tech Stack:** Python, FastAPI, Pydantic, SQLAlchemy async ORM, SQLite/Postgres, AIOSandbox,
LangGraph/LangChain MCP tooling.

---

## 1. Design Goals

### 1.1 What Stateless Means

DB 模式下，Gateway/Harness 进程不再依赖本地可写的这些文件作为运行态真实来源：

- `config.yaml`
- `extensions_config.json`
- legacy `mcp_config.json`
- custom agent `config.yaml`
- custom agent `SOUL.md`
- user `USER.md`
- `memory.json`
- local `skills/public` 和 `skills/custom` 的运行态可写内容

允许存在：

- 最小 bootstrap env
- 进程内 cache
- DB revision cache
- sandbox 内临时物化文件
- migration/rollback 时读取 legacy 文件

### 1.2 What Is Not Stateless In V1

以下能力第一版不声明为通用多节点 stateless：

- stdio MCP 的进程内 session state
- local bind-mounted skills root
- resolved secrets 本地落盘
- query-based memory recall

第一版重点是存储来源 DB 化和运行态本地文件依赖收敛。

---

## 2. Bootstrap Boundary

DB 模式不能做到零配置启动。以下信息必须来自 env 或极小 bootstrap 配置：

- `DEER_FLOW_CONFIG_SOURCE=file|db`
- `DEER_FLOW_DATABASE_URL` 或等价 DB 连接信息
- secret provider 基础配置
- MCP stdio command allowlist
- 进程级安全开关

新增模块建议：

`backend/packages/harness/deerflow/config/bootstrap.py`

职责：

- 判断当前 config source mode
- 提供 DB bootstrap URL
- 不依赖 `AppConfig`
- 不读取 runtime config DB 表

接口：

```python
from typing import Literal

ConfigSourceMode = Literal["file", "db"]


def get_config_source_mode() -> ConfigSourceMode:
    ...


def is_db_config_enabled() -> bool:
    ...


def get_bootstrap_database_url() -> str | None:
    ...
```

启动顺序：

1. bootstrap 层读取 env。
2. persistence engine 使用 bootstrap DB URL 初始化。
3. config source 读取 app runtime config。
4. Gateway/Harness runtime 根据 app config 初始化其余组件。

---

## 3. Revision Model

所有 DB-backed source/store 都必须暴露 revision 和 content hash。

建议通用结构：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RevisionedPayload:
    payload: dict[str, Any]
    revision: int
    content_hash: str
    updated_at: datetime
```

规则：

- 新 row 初始 `revision = 1`。
- 每次成功写入 `revision += 1`。
- `content_hash` 使用 canonical JSON 或文件 bytes hash。
- cache key 必须包含 `revision` 或 `content_hash`。
- 写入当前进程后立即 reset 相关 cache。
- 多 worker 第一版采用轻量 revision check。
- 后续可升级 Postgres `LISTEN/NOTIFY` 或 Redis pubsub。

Canonical JSON hash：

```python
import hashlib
import json
from typing import Any


def stable_json_hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
```

---

## 4. DB Schema

当前 persistence 已通过 `Base.metadata.create_all` 自动建表，并在
`backend/packages/harness/deerflow/persistence/models/__init__.py` 注册 ORM model。
新增表也要遵循这个模式。

### 4.1 Runtime Config

Create:

- `backend/packages/harness/deerflow/persistence/runtime_config/model.py`

Table: `runtime_configs`

Columns:

- `key: str`, primary key
- `payload_json: dict`
- `schema_version: str`
- `revision: int`
- `content_hash: str`
- `created_at: datetime`
- `updated_at: datetime`
- `updated_by: str | None`

Keys:

- `app`
- `extensions`
- `channel_runtime`

Notes:

- DB bootstrap connection itself cannot be stored only in `runtime_configs`.
- `database` section can be imported for visibility, but active DB connection must come from bootstrap.
- startup-only fields must preserve current reload boundary semantics.
- In DB config mode, UI-entered IM channel runtime credentials are read/written through `runtime_configs.channel_runtime`.
- `runtime_configs.channel_runtime.payload_json` keeps the existing provider-keyed shape: `{provider: provider_runtime_config}`.
- File mode remains backward-compatible through `.deer-flow/channels/runtime-config.json`.
- Writes to `channel_runtime` bump `revision`, `content_hash`, and `updated_by`, matching other runtime config rows.

### 4.2 MCP Servers

Create:

- `backend/packages/harness/deerflow/persistence/mcp/model.py`
- `backend/packages/harness/deerflow/config/mcp_store.py`

Table: `mcp_servers`

Columns:

- `name: str`, primary key
- `enabled: bool`
- `transport_type: str`
- `command: str | None`
- `args_json: list[str]`
- `url: str | None`
- `env_json: dict[str, str]`
- `headers_json: dict[str, str]`
- `oauth_json: dict | None`
- `description: str`
- `extra_json: dict`
- `revision: int`
- `content_hash: str`
- `created_at: datetime`
- `updated_at: datetime`

Constraints:

- `transport_type` is `stdio`, `http`, or `sse`.
- stdio config written through API must pass existing command allowlist.
- DB stores raw env refs such as `$GITHUB_TOKEN`, not resolved token values.
- GET masks env/header values and OAuth secrets.
- PUT preserves masked values from existing raw DB row.

### 4.3 Skill State And Files

Create:

- `backend/packages/harness/deerflow/persistence/skills/model.py`
- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`

Table: `skills`

Columns:

- `category: str`
- `name: str`
- `enabled: bool`
- `description: str`
- `license: str`
- `metadata_json: dict`
- `revision: int`
- `content_hash: str`
- `created_at: datetime`
- `updated_at: datetime`

Unique:

- `category + name`

Table: `skill_files`

Columns:

- `skill_category: str`
- `skill_name: str`
- `relative_path: str`
- `content_text: str | None`
- `content_bytes: bytes | None`
- `mime_type: str`
- `content_hash: str`
- `revision: int`
- `created_at: datetime`
- `updated_at: datetime`

Unique:

- `skill_category + skill_name + relative_path`

Table: `skill_history`

Columns:

- `id`
- `skill_category`
- `skill_name`
- `action`
- `payload_json`
- `created_at`

### 4.4 Agents

Create:

- `backend/packages/harness/deerflow/persistence/agents/model.py`
- `backend/packages/harness/deerflow/config/agent_store.py`

Table: `custom_agents`

Columns:

- `owner_user_id: str`
- `agent_name: str`
- `config_json: dict`
- `soul_text: str`
- `revision: int`
- `created_at: datetime`
- `updated_at: datetime`

Unique:

- `owner_user_id + agent_name`

Table: `user_profiles`

Columns:

- `owner_user_id: str`, primary key
- `profile_text: str`
- `revision: int`
- `created_at: datetime`
- `updated_at: datetime`

### 4.5 Memory

Create:

- `backend/packages/harness/deerflow/persistence/memory/model.py`

Modify:

- `backend/packages/harness/deerflow/agents/memory/storage.py`

Table: `memories`

Columns:

- `owner_user_id: str`
- `agent_scope: str`
- `memory_json: dict`
- `schema_version: str`
- `revision: int`
- `last_updated: datetime`
- `created_at: datetime`
- `updated_at: datetime`

Unique:

- `owner_user_id + agent_scope`

Scope convention:

- `agent_scope = ""` means user global memory.
- `agent_scope = agent_name` means user + agent memory.

---

## 5. Config Source Implementation

### 5.1 ConfigSource Interface

Create:

- `backend/packages/harness/deerflow/config/sources.py`

Interface:

```python
from typing import Protocol


class ConfigSource(Protocol):
    def load_app_config_payload(self) -> RevisionedPayload:
        ...

    def load_extensions_payload(self) -> RevisionedPayload:
        ...
```

Implementations:

- `FileConfigSource`
- `DbConfigSource`

### 5.2 AppConfig Loading

Modify:

- `backend/packages/harness/deerflow/config/app_config.py`

Keep:

- `AppConfig.from_file(...)`
- current file mode behavior

Add:

```python
@classmethod
def from_source(cls, source: ConfigSource) -> "AppConfig":
    ...
```

`get_app_config()` behavior:

- file mode uses current path/signature logic.
- db mode checks source revision/hash.
- db mode still validates through Pydantic.
- singleton side effects for title, memory, agents API, subagents, tool search, guardrails, checkpointer, stream bridge, ACP must remain aligned with loaded app config.

### 5.3 Startup-only Boundary

Continue using existing reload boundary definitions.

DB mode should expose changed startup-only fields as “requires restart” metadata for admin APIs and migration report.

Examples:

- `database`
- `checkpointer`
- `run_events`
- `stream_bridge`
- `sandbox`
- `channels`
- `channel_connections`

---

## 6. Extensions And MCP Implementation

### 6.1 ExtensionsConfig View

Modify:

- `backend/packages/harness/deerflow/config/extensions_config.py`

Keep:

- `ExtensionsConfig.from_file(...)` for migration and fallback.

Add:

```python
class ExtensionsConfigSource(Protocol):
    def load_extensions_config(self) -> RevisionedPayload:
        ...


def get_extensions_config_source() -> ExtensionsConfigSource:
    ...


def get_extensions_config_revision() -> tuple[int | None, str | None]:
    ...
```

Runtime code should use `get_extensions_config()` instead of direct `ExtensionsConfig.from_file()`.

Existing direct read call sites to change:

- MCP tool loading
- tool registry MCP inclusion
- ACP MCP bridge
- skill enabled state merge
- sandbox filesystem MCP allowed paths

### 6.2 MCP Store

Create:

- `backend/packages/harness/deerflow/config/mcp_store.py`

Interface:

```python
class McpStore(Protocol):
    def list_servers(self, include_disabled: bool = True) -> RevisionedPayload:
        ...

    def replace_servers(
        self,
        servers: dict[str, McpServerConfig],
        *,
        updated_by: str | None = None,
    ) -> RevisionedPayload:
        ...
```

### 6.3 Gateway API Semantics

Modify:

- `backend/app/gateway/routers/mcp.py`

GET:

- require admin
- read DB-backed `ExtensionsConfig`
- mask `env`
- mask `headers`
- remove OAuth `client_secret` and `refresh_token`

PUT:

- require admin
- validate stdio command allowlist
- preserve masked `***` values from current raw DB row
- empty string clears OAuth secret
- write DB
- reload extensions config
- reset MCP tools cache

### 6.4 MCP Cache

Modify:

- `backend/packages/harness/deerflow/mcp/cache.py`

Replace file mtime fields with:

```python
_config_revision: int | None = None
_config_hash: str | None = None
```

Stale check:

- if cache uninitialized, not stale
- if current revision/hash cannot be read, assume not stale and log debug
- if changed, reset cache

Reset behavior:

- clear `_mcp_tools_cache`
- clear `_cache_initialized`
- clear revision/hash
- close session pool
- reset session pool

### 6.5 MCP Session Pool

Modify either:

- `backend/packages/harness/deerflow/mcp/session_pool.py`

or keep session pool unchanged and guarantee reset on config write.

First implementation should:

- reset all pooled sessions on MCP config write
- record config hash at tool initialization

Optional later improvement:

- include `config_hash` in session key

### 6.6 MCP Stateless Policy

Production DB/stateless support:

- HTTP MCP: supported
- SSE MCP: supported
- stdio MCP: compatibility mode only

Documentation and migration dry-run must flag stdio MCP as not generally multi-node stateless.

---

## 7. Memory Implementation

### 7.1 DbMemoryStorage

Modify:

- `backend/packages/harness/deerflow/agents/memory/storage.py`

Add:

```python
class DbMemoryStorage(MemoryStorage):
    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        ...

    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        ...

    def save(
        self,
        memory_data: dict[str, Any],
        agent_name: str | None = None,
        *,
        user_id: str | None = None,
    ) -> bool:
        ...
```

Behavior:

- missing row returns `create_empty_memory()`
- save shallow-copies data before setting `lastUpdated`
- save does not mutate caller dict
- cache key stays `(user_id, agent_name)`
- cache value includes revision

### 7.2 Sync Bridge

Current `MemoryStorage` interface is synchronous.

V1 should keep the sync interface to avoid changing prompt injection, updater, and router all at once.

Implementation strategy:

- repository functions are async
- storage class runs async DB work through a sync bridge
- if no running loop exists, use `asyncio.run`
- if a running loop exists, use a worker thread and wait

Reason:

- `DynamicContextMiddleware` already offloads memory injection to a thread
- existing `get_memory_data()` call sites remain unchanged

### 7.3 Conflict Handling

Whole JSON memory writes can conflict.

Rules:

- write with `WHERE revision = expected_revision`
- if no row updated, reload latest memory
- for memory updater, replay parsed update once
- if replay conflicts again, return false and log warning
- never blind overwrite

### 7.4 Recall Compatibility

Do not change Phase 1 recall:

- still load full memory JSON for `(user_id, agent_name)`
- still use `format_memory_for_injection`
- still sort facts by confidence
- still obey `max_injection_tokens`
- still inject only once per thread as frozen snapshot

---

## 8. Skills Implementation

### 8.1 SkillStorage API Extension

Modify:

- `backend/packages/harness/deerflow/skills/storage/skill_storage.py`

Add methods:

```python
def read_skill_file(self, skill_name: str, category: str, relative_path: str) -> str | bytes:
    ...


def list_skill_file_manifest(self, skill_name: str, category: str) -> list[SkillFileManifest]:
    ...
```

Create:

```python
@dataclass(frozen=True)
class SkillFileManifest:
    relative_path: str
    content_hash: str
    mime_type: str
    size: int
```

LocalSkillStorage:

- reads files from disk
- validates relative path does not escape skill root

DbSkillStorage:

- reads DB rows
- returns text or bytes according to stored content

### 8.2 Progressive Loading

Loading stages:

1. Metadata list
   - load name, category, description, license, enabled, revision
   - do not load file contents
2. `SKILL.md` availability
   - before prompt exposes `/mnt/skills/.../SKILL.md`, materialize that file
3. Activation
   - slash activation reads `SKILL.md` through storage API
   - after activation, materialize full skill tree
4. Asset access
   - materializer writes assets on demand or during full skill tree materialization

### 8.3 Middleware Changes

Modify:

- skill activation middleware

Replace direct local path reads with:

- `storage.read_skill_file(skill.name, skill.category, "SKILL.md")`

Keep:

- same prompt text
- same enabled/disabled semantics
- same public/custom defaults

### 8.4 Sandbox Expectations

The model reads actual paths using tools such as `read_file`.

Therefore:

- DB content must be materialized before paths are advertised.
- There is no implicit DB lookup when sandbox reads `/mnt/skills/...`.
- Manifest is the source of truth for what is already materialized.

---

## 9. Agent Implementation

### 9.1 AgentStore

Create:

- `backend/packages/harness/deerflow/config/agent_store.py`

Interface:

```python
class AgentStore(Protocol):
    def load_agent_config(self, owner_user_id: str, agent_name: str) -> dict | None:
        ...

    def load_agent_soul(self, owner_user_id: str, agent_name: str) -> str | None:
        ...

    def list_agents(self, owner_user_id: str) -> list[AgentSummary]:
        ...

    def save_agent(self, owner_user_id: str, agent_name: str, config: dict, soul: str) -> None:
        ...

    def delete_agent(self, owner_user_id: str, agent_name: str) -> None:
        ...
```

Implement:

- `FileAgentStore`
- `DbAgentStore`

### 9.2 Compatibility

Preserve:

- agent name regex
- per-user custom agents
- legacy shared agent read-only fallback
- `assistant_id -> agent_name`
- model validation in update tool
- `skills=None` versus `skills=[]` semantics

Modify:

- `agents_config.py`
- Gateway agents router
- setup agent tool
- update agent tool

---

## 10. Sandbox Materializer

### 10.1 Component

Create:

- `backend/packages/harness/deerflow/sandbox/materializer.py`

Interface:

```python
class SandboxMaterializer:
    async def materialize(
        self,
        sandbox: AioSandbox,
        *,
        thread_id: str,
        user_id: str,
        agent_name: str | None,
        snapshot: RuntimeStateSnapshot,
    ) -> None:
        ...
```

Snapshot:

```python
@dataclass(frozen=True)
class RuntimeStateSnapshot:
    config_revision: int
    config_hash: str
    memory_revision: int | None
    memory_hash: str | None
    agent_revision: int | None
    agent_hash: str | None
    skills: tuple[SkillSnapshot, ...]
```

### 10.2 Paths

Materialize:

- `{skills.container_path}/{public,custom}/{skill}/SKILL.md` (default `/mnt/skills`)
- `{skills.container_path}/{public,custom}/{skill}/...` (default `/mnt/skills`)
- `/tmp/deerflow/context/memory.json`
- `/tmp/deerflow/context/agent/SOUL.md`
- `/tmp/deerflow/context/agent/config.json`
- `/tmp/deerflow/context/.manifest.json`

The manifest is stored under `/tmp/deerflow/context` because that path is writable by the default AIO sandbox user. Skill files are tracked in the same manifest but are written to `skills.container_path`, because prompt text and skill tooling expose that container path to the model.

Manifest example:

```json
{
  "version": 1,
  "config": {"revision": 12, "hash": "abc"},
  "agent": {"name": "researcher", "revision": 3, "hash": "def"},
  "memory": {"scope": "alice/researcher", "revision": 7, "hash": "ghi"},
  "skills": [
    {"category": "public", "name": "browser", "revision": 5, "hash": "jkl"}
  ]
}
```

### 10.3 Lifecycle

Call materializer after:

- sandbox create
- warm sandbox reclaim
- sandbox discover

And before:

- tools expose `/mnt/skills` paths
- model can read skill files

Rules:

- if manifest matches snapshot, no-op
- if revision/hash changed, write changed files
- if DB no longer contains a file, prune sandbox file
- remote AIO uses sandbox file API or tar/script upload
- local Docker AIO in DB/stateless mode mounts a per-thread writable skills directory at `skills.container_path`
- provisioner/K8s AIO deployments receive `extra_mounts` in the sandbox create payload; the bundled provisioner translates them to `hostPath` volumes and lets request-scoped mounts override default mount paths such as `/mnt/skills`
- DB/stateless mode does not rely on a host skill source bind mount; local Docker may use an empty writable scratch mount only to make `skills.container_path` writable while DB remains the source of truth

Provisioner create payload includes the mount contract:

```json
{
  "sandbox_id": "sandbox-42",
  "thread_id": "thread-42",
  "user_id": "user-7",
  "extra_mounts": [
    {
      "host_path": "/host/thread/skills",
      "container_path": "/mnt/skills",
      "read_only": false
    }
  ]
}
```

The bundled provisioner translates `host_path` into a `hostPath` volume with `DirectoryOrCreate`. Other provisioner implementations may translate it into a PVC, emptyDir, or equivalent K8s volume source. The observable requirement is that `container_path` exists and is writable by the AIO shell/file API user before runtime-context materialization runs.

### 10.4 Runtime Object Storage and PVC Removal

DB-backed config/memory/skills removes control-state files, but `/mnt/user-data` and `/mnt/acp-workspace` are runtime artifact paths. Strict stateless mode removes the runtime PVC by storing those files in an S3-compatible object store.

Object key layout:

```text
{prefix}/users/{user_id}/threads/{thread_id}/user-data/workspace/{path}
{prefix}/users/{user_id}/threads/{thread_id}/user-data/uploads/{path}
{prefix}/users/{user_id}/threads/{thread_id}/user-data/outputs/{path}
{prefix}/users/{user_id}/threads/{thread_id}/acp-workspace/{path}
```

Runtime config:

```yaml
runtime_storage:
  backend: object
  object_store:
    provider: s3
    endpoint_url: http://seaweedfs-s3:8333
    bucket: deerflow-runtime
    prefix: deerflow
    path_style: true
```

Implemented runtime paths:

- Gateway upload/list/delete routes write and read `ArtifactStore` in object mode.
- Gateway artifact preview/download reads `ArtifactStore`, including `.skill` archive member extraction.
- Embedded `DeerFlowClient` upload/list/delete/get_artifact uses `ArtifactStore` in object mode.
- `ThreadDataMiddleware` returns virtual `/mnt/user-data/...` paths instead of creating local thread directories.
- `UploadsMiddleware` lists historical uploads from `ArtifactStore`.
- `present_files` accepts virtual output paths without resolving `.deer-flow`.
- `view_image` reads image bytes from `ArtifactStore` while keeping MIME/magic/size checks.
- IM channel inbound files write object uploads; outbound artifacts are materialized to temporary files for existing channel adapters.
- Feishu resource downloads write object uploads and skip local sandbox sync.
- AIO sandbox lifecycle materializes object files after create/discover/reclaim and flushes workspace/uploads/outputs/ACP files before release.
- Object-mode sandbox creation uses PostgreSQL advisory locks instead of local lock files.
- Bundled provisioner omits `/mnt/user-data`, rejects `USERDATA_PVC_NAME`, rejects extra mounts under `/mnt/user-data` or `/mnt/acp-workspace`, and records `runtime_storage_backend` in the mount-contract hash.
- ACP gateway-side staging remains a file-mode/compatibility path; strict stateless mode runs ACP subprocesses inside named ephemeral AIO sandboxes and flushes their ACP workspace artifacts back through object storage.

Open-source object storage recommendation:

- SeaweedFS S3 Gateway is the default recommendation for lightweight K8s deployment.
- MinIO is acceptable when stronger S3 operational tooling is preferred.
- Ceph RGW is acceptable when Ceph already exists in the platform.

Strict static gate:

```bash
RUNTIME_STORAGE_BACKEND=object \
uv --directory backend run python scripts/check_stateless_live_gates.py --gate runtime_object_storage --json
```

### 10.5 ACP Sandbox-Native Execution

ACP agents can still run in legacy gateway mode for file-mode compatibility. Strict stateless deployments should configure ACP agents with `execution_mode: sandbox`, `sandbox_scope: isolated`, and an optional `sandbox_profile` that points to `sandbox.ephemeral_profiles`.

Example:

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  image: enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
  ephemeral_profiles:
    codex:
      image: registry.local/codex-acp:latest
      setup_commands:
        - npm install -g @zed-industries/codex-acp

acp_agents:
  codex:
    command: codex-acp
    args: ["--json"]
    description: Codex ACP adapter
    execution_mode: sandbox
    sandbox_scope: isolated
    sandbox_profile: codex
```

`AioSandboxProvider.acquire_ephemeral(name, profile)` creates a non-deterministic short-lived sandbox, applies the profile image/setup commands, and destroys the sandbox on release instead of parking it in the thread warm pool. Local Docker uses the profile image override and labels; remote provisioner payloads carry `name`, `image`, `labels`, and `ephemeral` so K8s can create the right Pod image.

ACP sandbox-native invocation bridges the ACP stdio connection to the AIO bash session API. The gateway owns only the transport bridge; the ACP subprocess itself runs in the sandbox and uses `/mnt/acp-workspace` there as its working directory. Object mode materializes and flushes only the ACP workspace root for this ephemeral sandbox, so leader-owned `/mnt/user-data` objects are not deleted by ACP sandbox release. Object-backed sandbox-native ACP requires a `thread_id`; without it, the call fails closed because object-store ownership cannot be derived. After a successful ACP workspace flush, the provider refreshes the same thread's active leader sandbox with `roots=(/mnt/acp-workspace,)`, making ACP outputs visible to the next leader operation without rematerializing or deleting `/mnt/user-data/{workspace,uploads,outputs}`.

The strict live-gate preflight rejects object-runtime configurations whose ACP agents still use gateway execution. The executable gate is opt-in and runs the sandbox-native ACP artifact visibility smoke:

```bash
DEER_FLOW_RUN_ACP_SANDBOX_NATIVE=1 \
uv --directory backend run python scripts/check_stateless_live_gates.py --gate acp_sandbox_native --run
```

---

## 11. Migration Tool

Create:

- `backend/scripts/import_runtime_state_to_db.py`
- `backend/scripts/import_runtime_artifacts_to_object_store.py`

Commands:

```bash
uv --directory backend run python scripts/import_runtime_state_to_db.py --dry-run
uv --directory backend run python scripts/import_runtime_state_to_db.py --apply
uv --directory backend run python scripts/import_runtime_state_to_db.py --apply --overwrite
uv --directory backend run python scripts/import_runtime_artifacts_to_object_store.py --state-dir /path/to/.deer-flow --dry-run --json
uv --directory backend run python scripts/import_runtime_artifacts_to_object_store.py --state-dir /path/to/.deer-flow --json
```

Dry-run must report:

- config files found
- extensions/MCP files found
- channel runtime config found
- custom agents found
- user profiles found
- memory files found
- skills found
- secret env refs
- resolved secret risks
- stdio MCP stateless risks
- schema validation errors
- overwrite conflicts

Secret/env-reference reporting is split by source:

- `config.risks` reports sensitive app config fields such as `models[].api_key`, `authorization`, token, secret, or password-like fields.
- `channel_runtime.risks` reports UI-entered channel runtime credentials from `.deer-flow/channels/runtime-config.json`.
- `extensions.risks` reports MCP env/header/OAuth fields and stdio MCP statefulness.

Apply behavior:

- default refuses to overwrite existing DB rows
- `--overwrite` replaces existing rows
- channel runtime config is imported into `runtime_configs.channel_runtime`
- each imported row starts revision at 1 unless preserving existing row
- output summary includes inserted, skipped, failed

---

## 12. Rollout Phases

### Phase 1: DB-backed Storage With File Default

Deliver:

- DB tables
- source/store interfaces
- DB implementations
- migration dry-run
- file mode remains default

Success:

- existing tests pass in file mode
- DB mode focused tests pass
- no runtime code requires local writable state in DB mode for implemented stores

### Phase 2: DB Mode Runtime

Deliver:

- Gateway APIs write DB stores
- config/extensions reload by revision
- MCP cache/session reset by revision
- memory/agents/skills read from DB

Success:

- DB mode can run through normal thread/run path
- memory recall unchanged
- MCP tool_search unchanged
- skills listing no longer loads all file contents

### Phase 3: Sandbox Stateless

Deliver:

- SandboxMaterializer
- skill `SKILL.md` pre-materialization
- full skill tree on activation
- agent/memory context materialization
- remote AIO no bind mount dependency

Success:

- sandbox contains expected files
- warm sandbox manifest diff works
- deleted files are pruned

### Phase 4: Enhancements

Deliver later:

- memory facts table
- query-based recall
- external secret store
- Postgres notify/pubsub
- stdio MCP sidecar or sandbox execution

---

## 13. Test Plan

### Config Tests

- file source parity
- DB source payload validation
- revision invalidation
- startup-only change detection
- malformed config rejection

### Agent Tests

- DB create/update/delete/list
- user isolation
- legacy fallback
- `assistant_id -> agent_name`
- setup/update tools

### Memory Tests

- missing DB row returns empty memory
- user global scope
- user + agent scope
- save does not mutate caller dict
- revision conflict retry
- recall output unchanged
- injection token budget unchanged

### Skills Tests

- metadata-only list
- read `SKILL.md` through storage API
- install archive into DB
- enabled state compatibility
- slash activation without local path dependency
- sandbox manifest materialization

### MCP Tests

- DB-backed ExtensionsConfig view
- GET masking
- PUT masked preserve
- stdio command allowlist
- OAuth token config
- cache reset on revision change
- session pool reset or config hash isolation
- ACP bridge
- filesystem allowed paths

### Sandbox Tests

- first materialization
- no-op when manifest unchanged
- diff update when revision changes
- prune deleted files
- remote AIO writes through API
- warm sandbox reclaim materializes missing files

### Final Verification

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check
git diff --check
```

If `uv` cannot access `~/.cache/uv` under sandbox, rerun the same command with approved elevated permissions.

---

## 14. Acceptance Criteria

- DB mode starts from bootstrap DB info without local writable runtime files.
- File mode remains backward-compatible.
- Existing Gateway API semantics remain stable.
- Memory recall remains behavior-compatible in Phase 1.
- Skills metadata can be listed without loading full file contents.
- Prompt-exposed skill paths exist in sandbox before model reads them.
- MCP config changes invalidate tool cache and session pool safely.
- Migration dry-run reports stateless incompatibilities before apply.
- Production docs state HTTP/SSE MCP support and stdio MCP compatibility limits.

---

## 15. Implementation Order

Recommended order:

1. Runtime config table and ConfigSource skeleton.
2. ExtensionsConfig source abstraction.
3. Memory DB storage.
4. Agent DB store.
5. MCP DB store and revision cache.
6. Skills DB storage and progressive loading.
7. SandboxMaterializer.
8. Migration dry-run and apply.
9. DB mode end-to-end smoke tests.

Rationale:

- Config and extensions unlock runtime source selection.
- Memory and agents are isolated and easier to validate.
- MCP has highest operational risk, so it should land after source abstractions.
- Skills must coordinate with sandbox materialization.
- Migration tooling should be validated against all stores after schemas settle.
