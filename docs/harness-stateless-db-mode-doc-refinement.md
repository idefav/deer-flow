# Harness Stateless DB Mode Documentation Refinement

本文是对以下两份文档的细化和优化建议：

- `docs/harness-stateless-db-mode-plan.md`
- `docs/harness-stateless-db-mode-technical-design.md`

它不是第三份互相竞争的实现方案，而是一个文档修订清单。目标是在进入代码实现前，把当前方案里容易产生歧义、实现风险高、或者两份文档之间不一致的地方提前收束掉。

---

## 1. 文档定位

### 1.1 现有文档职责

`harness-stateless-db-mode-plan.md` 应保留为总体改造计划，重点回答：

- 为什么要把 Harness 改成 DB-backed stateless runtime。
- 哪些本地文件状态要迁移到 DB。
- 各阶段交付边界是什么。
- 最终验收标准是什么。

`harness-stateless-db-mode-technical-design.md` 应保留为技术设计主文档，重点回答：

- 目标架构中的 source/store/materializer 如何分层。
- DB schema、接口、缓存失效、migration、测试如何设计。
- Memory、Skills、MCP、Agent、Sandbox 等子系统如何落地。

本文档应作为修订 gate，重点回答：

- 当前两份文档还有哪些问题。
- 哪些问题会阻塞实现。
- 每个问题应补到哪份文档。
- 推荐锁定哪些技术决策。

### 1.2 建议维护方式

- 不建议把三份文档长期写成三个完整方案。
- 本文档中的 P0/P1 问题修完后，应该把最终决策合并回技术设计文档。
- 总体计划文档只保留阶段、范围、风险和验收，不承载过细接口细节。
- 技术设计文档只保留已锁定的技术决策，不保留多种开放方案。

---

## 2. 总体 Review 结论

当前两份文档已经覆盖了核心方向：

- `config.yaml`、extensions/MCP、agents、memory、skills 都迁移为 DB-backed source/store。
- DB mode 下 Harness 进程不依赖本地可写运行态文件。
- AIOSandbox 通过 materialization 获取 memory、agent、skills 文件。
- Memory recall 第一阶段保持行为兼容。
- Skills 需要支持 metadata-first 的渐进式加载。
- MCP 在 DB 存储之后需要 revision/cache/session/secret 管理。

但当前文档还不能直接进入代码实现，主要原因是：

- Bootstrap 启动闭环没有完全拆清。
- Memory 的 sync/async bridge 方案仍有运行时风险。
- Skills 渐进加载和 sandbox lazy creation 有生命周期冲突。
- MCP 的 DB 存储只覆盖了配置形态，没有完全覆盖 runtime cache、session、secret、interceptor 风险。
- schema 字段名和 hash 命名在两份文档中不一致。
- 技术设计中仍存在 `...` 作为伪代码占位。

建议把 P0/P1 修完后，再把技术设计拆成 task-by-task implementation plan。

---

## 3. P0 必须修正问题

### 3.1 Bootstrap 启动闭环

**当前问题**

技术设计已经指出 DB mode 需要 `DEER_FLOW_CONFIG_SOURCE` 和 `DEER_FLOW_DATABASE_URL` 这类 bootstrap 信息，但还没有把它和现有 persistence engine 的初始化关系写到可实现级别。

当前架构中，persistence engine 依赖 `AppConfig.database` 初始化；而 DB mode 又希望 `AppConfig` 从 DB 中读取。若不拆分 bootstrap engine，就会出现循环依赖：

1. 读取 DB-backed `AppConfig`。
2. 需要 DB engine。
3. DB engine 又需要 `AppConfig.database`。

**影响**

- DB mode 可能无法冷启动。
- 实现者可能错误地把 active DB connection 存到 `runtime_configs.database`。
- 多 worker 启动时可能出现 file mode 和 DB mode 混用。

**推荐修订**

在技术设计文档中新增明确启动序列：

1. `config/bootstrap.py` 只读取 env 或极小 bootstrap file。
2. bootstrap 层构造 active database settings。
3. persistence engine 使用 bootstrap database settings 初始化。
4. `DbConfigSource` 通过已初始化的 engine 读取 `runtime_configs.app`。
5. `AppConfig.from_source()` validation 成功后初始化 Harness 其他 runtime singleton。

同时明确：

- `runtime_configs.database` 可以迁移入 DB 作为显示和审计信息。
- `runtime_configs.database` 不能作为当前进程 active engine 的唯一来源。
- 如果 DB 中的 `database` section 与 bootstrap DB URL 不一致，admin API 应报告 `requires_restart` 或 `bootstrap_mismatch`，不能热切换当前 engine。

**目标文档**

- 修改 `harness-stateless-db-mode-technical-design.md` 的 `Bootstrap Boundary` 章节。
- 在 `harness-stateless-db-mode-plan.md` 的风险章节保留一句摘要即可。

**阻断等级**

P0。未修正前不应进入 DB config source 实现。

### 3.2 MemoryStorage sync bridge

**当前问题**

技术设计中提出：

- repository functions 是 async。
- storage class 通过 sync bridge 调用 async DB。
- 没有 running loop 时用 `asyncio.run`。
- 有 running loop 时用 worker thread。

这个描述不够安全。SQLAlchemy async engine、session factory、connection pool 和 event loop/thread 的关系需要被明确约束，否则可能出现：

- async engine 跨 loop 或跨 thread 使用。
- running loop 内阻塞等待 worker thread 导致死锁风险。
- memory updater、router、middleware 在不同执行上下文下行为不一致。

**影响**

- Memory recall 和 update 是用户体验核心路径，一旦 bridge 不稳定，会出现偶发注入失败或更新丢失。
- 文档虽然说保持兼容，但实现者仍需要自行选择 sync/async 方案。

**推荐修订**

技术设计必须在以下三种方案中锁定一种，不保留开放选择：

方案 A：同步 DB engine 专用于 `DbMemoryStorage`

- `MemoryStorage` 接口保持同步。
- `DbMemoryStorage` 使用 SQLAlchemy sync engine/session。
- async persistence engine 仍服务其他 async repository。
- 优点是最小化调用链改动。
- 缺点是维护两套 engine/session。

方案 B：Memory API async-first

- 将 memory storage、updater、router、middleware 全链路改为 async。
- 彻底移除 sync bridge。
- 优点是架构一致。
- 缺点是改动面大，不适合作为 V1 兼容改造。

方案 C：thread-owned async engine

- `DbMemoryStorage` 内部维护一个专用 worker thread。
- async engine 和 session factory 只在该 thread 的 event loop 中创建和使用。
- 所有 sync `load/save/reload` 通过 queue/future 提交到该 thread。
- 优点是保持 sync interface，又避免跨 loop 使用 async engine。
- 缺点是实现复杂，需要 shutdown hook 和测试。

推荐 V1 选择方案 A 或 C。若优先稳妥和实现速度，选择方案 A；若强烈希望复用 async repository，选择方案 C。

**目标文档**

- 修改 `harness-stateless-db-mode-technical-design.md` 的 `Memory Implementation / Sync Bridge`。
- 在 implementation order 中把 Memory bridge 作为独立任务，不要藏在 `DbMemoryStorage` 一句里。

**阻断等级**

P0。未锁定前不应实现 `DbMemoryStorage`。

---

## 4. P1 高优先级修正问题

### 4.1 DB schema 可实现性不足

**当前问题**

技术设计列出了表和字段，但缺少足够具体的 ORM 实现细节：

- JSON 字段在 SQLite/Postgres 下的类型策略。
- `content_hash`、`revision`、`updated_at` 的索引策略。
- `skill_files.content_bytes` 是否允许无限放入 DB。
- `relative_path`、`agent_name`、`mcp server name` 的长度限制。
- 大文件、二进制文件、压缩包导入的大小限制和失败行为。

**影响**

- SQLite 和 Postgres 行为可能不一致。
- Skills 文件若全量塞进 DB，可能导致 DB 体积快速膨胀。
- migration 导入阶段可能把不适合 DB 存储的 asset 直接写入。

**推荐修订**

技术设计中为每张表补充：

- SQLAlchemy column 类型。
- 主键、唯一约束、常用索引。
- JSON 类型兼容策略。
- 时间字段 timezone 策略。
- 字符串长度上限。
- blob/text size limit。

对 `skill_files` 明确 V1 策略：

- 默认允许小文件进入 DB。
- 单文件超过阈值时 dry-run 报告风险。
- 阈值建议先设为配置项，例如 `DEER_FLOW_DB_SKILL_FILE_MAX_BYTES`，默认 1 MiB 或 5 MiB。
- 超大 assets 在 V1 中拒绝导入或标记为 unsupported，不在文档里隐含支持。
- 后续阶段再引入 object storage，而不是 V1 默认实现。

**目标文档**

- 修改技术设计的 `DB Schema` 章节。
- migration dry-run 章节增加大文件风险报告。

### 4.2 ConfigSource 和 ExtensionsConfigSource 职责重叠

**当前问题**

技术设计里同时有：

- `ConfigSource.load_extensions_payload()`
- `ExtensionsConfigSource.load_extensions_config()`
- `McpStore.list_servers()`

这会导致 extensions 相关运行态有多个入口。实现者可能不知道 MCP、skills enabled state、extensions view 应从哪一层读。

**影响**

- cache key 和 revision 来源可能不统一。
- MCP tools、ACP bridge、sandbox allowed paths 可能仍然绕过新 source。
- 后续 file mode/DB mode 的 fallback 逻辑容易分叉。

**推荐修订**

锁定一条职责边界：

- `ConfigSource` 只负责 app runtime config。
- `ExtensionsConfigSource` 负责生成完整 `ExtensionsConfig` view。
- `McpStore` 和 `SkillStateStore` 只作为 `ExtensionsConfigSource` 的下层 repository，不被 runtime call sites 直接调用。
- 所有 runtime call sites 只读 `get_extensions_config()` 或等价 source facade。

同时删除或改写 `ConfigSource.load_extensions_payload()`，避免双入口。

**目标文档**

- 修改技术设计的 `Config Source Implementation` 和 `Extensions And MCP Implementation`。
- 总体计划文档中只保留 `ConfigSource`、`ExtensionsConfigSource` 两个抽象，不展开重叠方法。

### 4.3 Sandbox materializer 生命周期不够明确

**当前问题**

文档说 materializer 在 sandbox create、warm reclaim、discover 后调用，并且在 model 能读文件前调用。但没有明确具体 hook 和失败语义。

**影响**

- 如果 materializer 失败但工具仍暴露 `/mnt/skills`，模型会看到不存在的文件路径。
- lazy sandbox 模式下，prompt 可能早于 sandbox 创建。
- warm sandbox 复用时可能读到上一次 run/thread 的旧文件。

**推荐修订**

技术设计中明确 materializer 的调用点：

- sandbox 首次创建后必须 materialize base snapshot。
- warm sandbox 绑定到新的 run/thread 前必须比对 manifest。
- 每次 skills activation 前必须确保该 skill 完整 materialized。
- 每次暴露 `/mnt/skills` prompt 前必须确保至少 `SKILL.md` 已存在。

失败策略建议：

- DB/stateless mode 下 fail closed。
- 如果必需文件物化失败，则不暴露对应 skill path，并返回可观测错误。
- 不允许在 DB/stateless mode 下 silent fallback 到 host bind mount。

**目标文档**

- 修改技术设计的 `Sandbox Materializer` 章节。
- 修改 plan 文档风险章节，强调 prompt path 和 sandbox lifecycle 的耦合。

### 4.4 Skills 渐进式加载与 lazy sandbox 冲突

**当前问题**

方案希望 skills 可以：

- metadata-first。
- 暴露 `/mnt/skills/.../SKILL.md`。
- slash activation 后再完整物化。

但当前 sandbox 可能是工具调用时 lazy acquire。若 prompt 阶段已经展示 `/mnt/skills/.../SKILL.md`，而 sandbox 尚未创建，就无法保证路径存在。

**影响**

- 这会直接影响用户问到的“Skills 改成 DB 存储还能渐进式加载么”。
- 答案应是“可以，但 prompt path 暴露策略必须改变或提前物化 SKILL.md”。

**推荐修订**

文档必须锁定 V1 行为，推荐两种之一：

推荐方案：DB/stateless mode 下提前创建 sandbox 并预物化 enabled skills 的 `SKILL.md`

- metadata list 仍从 DB 轻量读取。
- prompt 构建前确保 sandbox 存在。
- 只物化 enabled skills 的 `SKILL.md`，不物化全部 assets。
- slash activation 后再物化完整 skill tree。

备选方案：prompt 不直接暴露真实 `/mnt/skills` 文件路径

- prompt 只描述 skill name/description。
- 模型通过专门 tool 或 activation 流程请求 skill content。
- 优点是保持 sandbox lazy。
- 缺点是改变现有 prompt/tool 交互模式，V1 兼容风险更高。

建议 V1 选择提前物化 `SKILL.md`，因为它更接近现有行为。

**目标文档**

- 修改技术设计的 `Skills Implementation / Progressive Loading`。
- 在 acceptance criteria 中增加“DB/stateless mode prompt 暴露的 skill path 必须已存在”。

### 4.5 MCP DB 存储后的 runtime 风险需要展开

**当前问题**

当前文档已经提到 stdio MCP、cache reset、secret masking，但还不够完整。MCP 从文件迁到 DB 后，不只是 config 存储变化，还影响：

- 多 worker cache invalidation。
- session pool 的生命周期。
- OAuth token refresh 的写回位置。
- masked value round-trip。
- `mcpInterceptors` 的动态 import 风险。
- ACP bridge 和 sandbox allowed paths 的配置读取路径。
- stdio MCP 的进程态和多节点部署边界。

**影响**

- MCP 是 stateless 改造中风险最高的子系统之一。
- 如果只实现 DB config，不处理 runtime state，会产生“配置在 DB，但运行仍是单进程状态”的误解。

**推荐修订**

技术设计中新增 MCP 风险矩阵：

| 问题 | V1 决策 | 后续增强 |
| --- | --- | --- |
| HTTP/SSE MCP | DB/stateless 正式支持 | 增加健康检查和连接池指标 |
| stdio MCP | compatibility mode，不承诺多节点 stateless | sidecar 或 sandbox-hosted MCP |
| cache invalidation | 写入后 reset 本进程，读路径轻量 revision check | Postgres NOTIFY 或 Redis pubsub |
| session pool | config revision/hash 改变时关闭并重建 | session key 包含 config hash |
| secrets | DB 存 env refs 和 masked payload，不存 resolved secret | external secret store |
| OAuth token | 明确 token 存储表或仍走现有路径 | secret store + encrypted refresh token |
| interceptors | migration/allowlist only | 插件签名和权限模型 |

同时明确：

- 所有 runtime call sites 不再直接调用 `ExtensionsConfig.from_file()`。
- `ExtensionsConfig.from_file()` 只用于 file mode、migration、rollback。
- DB mode 中 `mcp/cache.py` 的 stale 判断基于 revision/hash，而不是 mtime。

**目标文档**

- 修改技术设计的 `Extensions And MCP Implementation`。
- plan 文档中保留 MCP stateless support boundary。

### 4.6 Memory 分层与 DB 存储边界

**当前问题**

现有文档说明 memory 有 user global 和 user+agent 两层，也说明 V1 使用完整 JSON blob。但还可以补得更明确：

- `agent_scope = ""` 的含义。
- legacy global memory 的迁移规则。
- DB 中是否保留所有 memory 层级。
- recall 读取哪个 scope。
- 写 memory 时是否同时写 global 和 agent scope。

**影响**

- 容易把 memory DB 化误解为拆 facts 表或 embedding recall。
- 容易在 migration 时丢掉 legacy memory。

**推荐修订**

技术设计补充：

- V1 可以完全放入 DB，但按现有层级存整份 JSON。
- 不改变 recall 选择逻辑：仍按现有 `(user_id, agent_name)` 语义读取。
- legacy shared/global memory 在 migration dry-run 中单独报告，apply 时按明确 owner 规则导入。
- memory updater 成功后必须写 DB，不能只更新文件或进程 cache。
- DB row 的 `last_updated` 对齐现有 `lastUpdated` 字段。

**目标文档**

- 修改技术设计的 `Memory Implementation`。
- plan 文档 current state 可保留一句摘要。

### 4.7 Memory context 膨胀和注入上限

**当前问题**

用户已经明确问过“现在把所有 Memory 注入到上下文会不会导致上下文膨胀或者溢出，项目是怎么处理的”。文档中虽然写了 `max_injection_tokens`，但需要把 DB 化后的影响写清楚。

**影响**

- DB 化可能被误认为可以无限保存并无限注入。
- 实现者可能把 DB 中更多 memory 层级一起注入，导致上下文膨胀。

**推荐修订**

技术设计明确：

- DB 只改变持久化来源，不改变 V1 注入策略。
- 注入仍经过 `format_memory_for_injection()` 和 `max_injection_tokens`。
- 同一 thread 内 memory snapshot 仍冻结，避免运行中持续膨胀。
- 不在 V1 引入全库 recall、embedding recall 或跨 agent memory 聚合注入。
- materialized `/tmp/deerflow/context/memory.json` 只是给 sandbox 工具读取的 snapshot，不等于全部注入 LLM 上下文。

**目标文档**

- 修改技术设计的 `Memory Implementation / Recall Compatibility`。
- acceptance criteria 加入“DB mode 不增加默认注入 token 上限”。

---

## 5. P2 文档一致性与可读性问题

### 5.1 字段命名不一致

当前两份文档中存在以下命名不一致：

- `env_refs_json` vs `env_json`
- `config_hash` vs `content_hash`
- `runtime_configs.config_hash` vs `RevisionedPayload.content_hash`

建议统一：

- 所有内容哈希统一为 `content_hash`。
- MCP env 字段统一为 `env_json`，但字段说明必须写明其中保存的是 env refs 或 literal values，resolved secret 不落库。
- 如果需要表达“配置哈希”，在描述中使用 config content hash，不新增 `config_hash` 字段。

### 5.2 技术设计中的 `...` 占位

技术设计中存在多个伪代码 `...`。作为设计说明可以接受，但作为实现前文档不够明确。

建议：

- interface 示例中保留函数签名，但不要把 `...` 当作实现说明。
- 每个接口后补充输入、输出、异常、缓存语义。
- 对必须实现的 helper 给出明确行为，不要求给完整代码。

### 5.3 两份文档内容重叠

当前 plan 文档也包含很多 schema 和 task 细节，technical design 文档也包含 rollout 和 acceptance criteria。建议整理：

- plan 文档保留 roadmap、phase、scope、risks、acceptance criteria。
- technical design 文档保留具体 schema、interface、data flow、failure mode、test matrix。
- refinement 文档保留 review 和修订任务，修订完成后可作为历史记录保留。

### 5.4 测试计划粒度不均

当前测试计划覆盖面较广，但缺少哪些是文档修订前必须能映射到实现任务的 gate。

建议把测试分成：

- Design gate tests：实现前必须有测试策略。
- Phase 1 compatibility tests：file mode 和 DB mode 行为兼容。
- Phase 2 stateless tests：sandbox materialization 和 no-local-writable-state。
- Phase 3 enhancement tests：query recall、secret store、pubsub、MCP sidecar。

---

## 6. 建议锁定的技术决策

### 6.1 Bootstrap 决策

- DB mode 必须有 env/bootstrap 来源的 active database settings。
- `runtime_configs.database` 不驱动当前进程 active engine。
- DB mode 启动失败时应 fail fast，不 fallback 到 file mode，除非显式设置 file mode。

### 6.2 Config/Extensions 决策

- `ConfigSource` 只加载 app runtime config。
- `ExtensionsConfigSource` 只负责生成完整 extensions view。
- MCP 和 skill enabled state 可以分表存储，但 runtime call sites 只读 extensions facade。

### 6.3 Memory 决策

- V1 Memory 完全可以放入 DB，但按现有层级保存整份 JSON。
- V1 不引入 query recall、embedding recall、facts 拆表。
- V1 不改变默认 memory 注入策略和 token 上限。
- `MemoryStorage` sync bridge 必须在实现前锁定为同步 engine 或 thread-owned async engine。

### 6.4 Skills 决策

- DB 存储仍支持渐进式加载。
- metadata/list 不加载文件内容。
- prompt 暴露 skill path 前必须保证 `SKILL.md` 已物化。
- slash activation 后再物化完整 skill tree。
- V1 对超大 skill assets 设置导入限制，不隐含支持无限 blob。

### 6.5 Sandbox 决策

- DB/stateless mode 不依赖 host bind mount。
- materializer 根据 manifest 做 idempotent write 和 prune。
- 必需 runtime 文件物化失败时 fail closed。
- warm sandbox 复用前必须校验 manifest，避免跨 run/thread 泄漏旧状态。

### 6.6 MCP 决策

- HTTP/SSE MCP 是 V1 stateless 正式支持目标。
- stdio MCP 是 compatibility mode，不承诺多节点 stateless。
- MCP config 写入后必须 reset tool cache 和 session pool。
- secrets 存 env refs 或 masked payload，不存 resolved secret。
- `mcpInterceptors` 不开放普通 API 任意 DB 写入。

---

## 7. 建议修订任务

### Task A: 修订总体计划文档

目标文件：

- `docs/harness-stateless-db-mode-plan.md`

建议修改：

- 保留 scope、phase、risks、acceptance criteria。
- 删除或压缩过细 schema 细节，避免和 technical design 双写。
- 风险章节增加：
  - bootstrap loop。
  - memory bridge。
  - lazy sandbox 与 skill prompt path。
  - MCP runtime statefulness。
- acceptance criteria 增加：
  - DB mode 不增加默认 memory injection token 上限。
  - DB/stateless mode prompt 暴露的 skill path 必须已物化。
  - stdio MCP compatibility mode 已在 migration dry-run 中标记。

### Task B: 修订技术设计文档

目标文件：

- `docs/harness-stateless-db-mode-technical-design.md`

建议修改：

- Bootstrap 章节补充 active engine 初始化顺序。
- Revision model 统一 `content_hash` 命名。
- DB schema 章节补充 ORM 类型、索引、限制和 SQLite/Postgres 兼容性。
- Config/Extensions 章节取消双 source 入口。
- Memory 章节锁定 sync bridge 策略。
- Skills 章节锁定 prompt path 与 sandbox creation 策略。
- MCP 章节增加 runtime 风险矩阵。
- Sandbox 章节补充具体 lifecycle hook 和 fail-closed 语义。
- 清理所有作为实现占位的 `...`。

### Task C: 增加实现前 gate

建议在技术设计末尾新增 `Pre-implementation Gate`：

- P0 问题已全部修正。
- P1 问题已有明确实现决策。
- 字段命名一致。
- 每个 acceptance criterion 都能映射到测试。
- migration dry-run 可以报告所有不支持或高风险资源。

---

## 8. 修订后验收标准

修订后的文档应满足：

- 没有 bootstrap 循环依赖。
- 没有 `ConfigSource` 与 `ExtensionsConfigSource` 双入口歧义。
- `MemoryStorage` DB 实现策略唯一且可测试。
- DB-backed Memory 的层级、写入、recall、注入上限都保持 V1 兼容。
- Skills DB 存储支持渐进式加载，但 prompt path 和 sandbox lifecycle 不冲突。
- MCP DB 存储后的 cache、session、secret、stdio、interceptor 风险都有明确处理。
- DB schema 对 SQLite/Postgres、JSON、blob/text、hash/revision 有明确约束。
- 文档中不再出现未解释的 `TODO`、`TBD`、`...` 实现占位。
- `env_json`、`content_hash` 等字段命名全局一致。

建议用以下命令做文档自检：

```bash
rg -n "TODO|TBD|\\.\\.\\." docs/harness-stateless-db-mode*.md
rg -n "env_refs_json|env_json|config_hash|content_hash" docs/harness-stateless-db-mode*.md
git diff --check
```

第一条命令在修订完成前可以有输出，但每一处输出都必须是刻意保留并有解释；修订完成后不应再有未解释的实现占位。

---

## 9. 建议实施顺序

1. 先按本文档修订 technical design 的 P0 项。
2. 再修订 MCP、Skills、Sandbox 三个 P1 高风险章节。
3. 统一 schema 字段命名和 hash/revision 规则。
4. 压缩 plan 文档中和 technical design 重复的细节。
5. 增加 pre-implementation gate。
6. 最后再把技术设计拆成真正可执行的 implementation plan。

这样做的收益是：后续实现时，工程师不需要再现场判断 bootstrap、memory bridge、skill materialization、MCP stateless 边界这些关键决策，可以直接按文档进入 TDD 和分阶段落地。
