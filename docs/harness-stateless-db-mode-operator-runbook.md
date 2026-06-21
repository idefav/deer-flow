# Harness Stateless DB Mode Operator Runbook

Date: 2026-06-20

This runbook covers the V1 migration from file-backed Harness runtime state to DB-backed stateless mode.

It is intentionally operational, not architectural. For design details, see:

- `docs/harness-stateless-db-mode-plan.md`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

## Scope

The V1 import command handles user-editable runtime state:

- `config.yaml`
- `extensions_config.json`
- legacy MCP definitions
- `USER.md`
- top-level default-agent `SOUL.md`
- custom agents under legacy `agents/{agent}` and per-user `users/{user}/agents/{agent}`
- memory files at global, user, and agent scopes
- public and custom skills under `skills/public` and `skills/custom`

Strict DB mode requires public skills to be seeded/imported into the DB too. `DbSkillStorage` no longer reads public skills from the gateway filesystem in DB mode; missing public skill seed data fails closed instead of silently depending on a deployment artifact.

## Important Owner Mapping

Legacy global files do not contain a user identity. During migration they are mapped to the compatibility owner `default`.

| Legacy source | DB target |
| --- | --- |
| `{state_dir}/USER.md` | `user_profiles.owner_user_id = "default"` |
| `{state_dir}/SOUL.md` | `default_agent_souls.owner_user_id = "default"` |
| `{state_dir}/memory.json` | `memories.owner_user_id = "default"`, global scope |
| `{state_dir}/agents/{agent}` | `custom_agents.owner_user_id = "default"` |
| `{state_dir}/agents/{agent}/memory.json` | `memories.owner_user_id = "default"`, `agent_scope = "{agent}"` |

Treat `default` as a migration compatibility owner, not a real user account. After migration, teams that need per-user default-agent SOUL content should copy or update the value through:

```bash
PUT /api/default-agent-soul
```

with the intended authenticated/effective user.

## Preflight Checklist

Before applying migration:

1. Stop or drain writer traffic that may update file-backed state.
2. Back up the source state directory and DB target.
3. Set the target DB explicitly, either with `DEER_FLOW_DATABASE_URL` or `--database-url`.
4. Run dry-run inventory and review:
   - `preflight.ok`
   - `preflight.errors`
   - `preflight.conflicts`
   - `summary.risks`
   - `config.risks`
   - `channel_runtime`
   - `skills[].import_action`
   - `skills[].storage_boundary`
   - `skills[].runtime_artifact_required`
   - `skills[].deployment_artifact`
   - `extensions.risks`
   - `extensions.mcp_compatibility`
5. Resolve source errors before apply. `--overwrite` only relaxes existing DB conflicts; it does not import malformed source files.

## Postgres Schema Migration

For Postgres deployments, apply the stateless DB schema before running the runtime-state import:

```bash
psql "$DEER_FLOW_DATABASE_URL" \
  -f backend/packages/harness/deerflow/persistence/migrations/versions/20260621_0001_stateless_db_mode_pg.sql
```

The SQL migration creates the DB-backed runtime-state tables for config, agents, memory, MCP, and skills using Postgres-native `JSONB` and `TIMESTAMPTZ` columns. It is idempotent for table and index creation and should be applied to the target application database, not the LangGraph checkpointer schema.

The migration intentionally does not create foreign keys or unique keys. Business-key uniqueness for agent rows, memory scopes, and skill rows is enforced in the store code by selecting the canonical row and pruning duplicates on write/delete paths.

## Dry Run

Use dry-run first:

```bash
uv --directory backend run python scripts/import_runtime_state_to_db.py \
  --project-root /path/to/project \
  --state-dir /path/to/.deer-flow \
  --skills-root /path/to/project/skills \
  --database-url sqlite:////absolute/path/to/runtime.db
```

For Postgres:

```bash
uv --directory backend run python scripts/import_runtime_state_to_db.py \
  --project-root /path/to/project \
  --state-dir /path/to/.deer-flow \
  --skills-root /path/to/project/skills \
  --database-url postgresql+psycopg://user:password@host:5432/dbname
```

Expected dry-run behavior:

- no DB file is created for a missing sqlite target.
- invalid `config.yaml`, `extensions_config.json`, agent config, memory JSON, or skill metadata appears in `preflight.errors`.
- invalid channel runtime config JSON at `.deer-flow/channels/runtime-config.json` appears in `preflight.errors`.
- existing target rows appear in `preflight.conflicts`.
- app config secret fields, channel runtime credentials, stdio MCP, env references, and resolved secrets appear in risk sections. `config.risks` covers sensitive `config.yaml` fields such as model `api_key`; `channel_runtime.risks` covers UI-entered channel runtime credentials; `extensions.risks` covers MCP env/header/OAuth fields. `extensions.mcp_compatibility` records each MCP server's enabled state, transport, strict-stateless verdict, and runtime mode. `env-ref` entries show values that depend on deployment environment variables; `resolved-secret` entries flag plaintext values that should be converted to env references or a secret-backed path before apply.
- public and custom skills show `import_action: import-to-db`, `storage_boundary: db`, `runtime_artifact_required: false`, and `deployment_artifact: null`.

## Apply Without Overwrite

Use the conservative default when importing into an empty or prepared target DB:

```bash
uv --directory backend run python scripts/import_runtime_state_to_db.py \
  --project-root /path/to/project \
  --state-dir /path/to/.deer-flow \
  --skills-root /path/to/project/skills \
  --database-url sqlite:////absolute/path/to/runtime.db \
  --apply \
  --updated-by runtime-state-import
```

If target rows already exist, apply fails before mutation and reports blocking conflicts. This is the intended safe default.

Review these apply fields after a normal import:

- `applied.app_config`
- `applied.channel_runtime_config`
- `applied.extensions_config`
- `applied.mcp_servers`
- `applied.user_profiles`
- `applied.default_agent_souls`
- `applied.agents`
- `applied.memory_files`
- `applied.skills`

## Apply With Overwrite

Use overwrite only when replacing existing DB rows is intentional:

```bash
uv --directory backend run python scripts/import_runtime_state_to_db.py \
  --project-root /path/to/project \
  --state-dir /path/to/.deer-flow \
  --skills-root /path/to/project/skills \
  --database-url sqlite:////absolute/path/to/runtime.db \
  --apply \
  --overwrite \
  --updated-by runtime-state-import
```

Review these apply fields after overwrite:

- `applied.overwritten_conflicts`
- `applied.overwritten_by_resource`
- `applied.restart_required`
- `preflight.conflicts`

The overwrite report is based on conflicts detected before apply. It is not a full audit of whether every imported value changed content.

When overwrite replaces an existing DB app config row and changes startup-only fields, the report includes restart metadata:

```json
{
  "applied": {
    "restart_required": {
      "app_config": {
        "fields": ["log_level", "sandbox"],
        "reasons": {
          "log_level": "apply_logging_level() runs only during app.py startup...",
          "sandbox": "get_sandbox_provider() caches the provider singleton..."
        }
      }
    }
  }
}
```

If `applied.restart_required` is present, plan a gateway process restart before relying on the changed startup-only values. Non-startup config changes can still be picked up by revision-aware reload paths, but infrastructure singletons remain bound to their startup snapshot.

## DB Mode Startup

After a successful import, enable DB-backed config mode:

```bash
export DEER_FLOW_CONFIG_SOURCE=db
export DEER_FLOW_DATABASE_URL=sqlite:////absolute/path/to/deerflow.db
```

Then start the gateway with the usual deployment command.

In DB config mode, UI-entered IM channel runtime credentials use `runtime_configs.channel_runtime` as the source of truth. New channel runtime writes from the Gateway update that DB row; file mode keeps the backward-compatible `.deer-flow/channels/runtime-config.json` path.

Startup-only fields still require process restart when changed. DB mode changes the source of truth; it does not make every runtime singleton hot-reloadable.

Admin tooling can inspect the authoritative reload boundary through the Gateway:

```bash
curl -sS -H "Cookie: access_token=..." \
  http://localhost:8000/api/config/reload-boundary
```

The response contains `startup_only_prefix` and a `fields` map keyed by top-level config field. Each field has `requires_restart: true` and the same reason text used by the schema and migration report. This endpoint is read-only and requires an admin user.

## Compatibility And Strict Runtime Context

V1 supports two sandbox runtime-context materialization modes:

| Mode | `sandbox.runtime_context_fail_closed` | Behavior |
| --- | --- | --- |
| Compatibility rollout | `false` | Record `sandbox_context_error` if DB-backed runtime context cannot be materialized, then continue sandbox acquisition. |
| Strict stateless | `true` | Record `sandbox_context_error` and fail sandbox initialization when DB-backed runtime context cannot be materialized. |

Use compatibility mode during initial rollout, mixed file/DB operation, or when preserving availability is more important than guaranteeing prompt-visible context files.

Use strict stateless mode when memory, agent SOUL, custom skills, or other DB-backed context files must be present before sandbox tools run.

Runtime context files are materialized under `/tmp/deerflow/context` by default. This path is writable by the default AIO sandbox user and avoids depending on a bind mount for `/mnt/deerflow`.

Skill files are materialized to `skills.container_path` because prompts expose paths such as `/mnt/skills/custom/<skill>/SKILL.md`. In DB/stateless mode, local Docker AIO sandboxes mount a per-thread empty skills directory at that path with write permission, then the materializer writes DB skill content through the sandbox file API. This mount is scratch space only; DB remains the source of truth.

For remote provisioner or K8s AIO deployments, provide the same writable `skills.container_path` volume/emptyDir with permissions for the AIO shell/file API user. If the platform cannot make `/mnt/skills` writable, set `skills.container_path` to a writable in-container path such as `/tmp/deerflow/skills` and verify prompts/tools use that configured path consistently.

Provisioner mode receives the gateway-computed mount requirements in `POST /api/sandboxes` as `extra_mounts`:

```json
{
  "extra_mounts": [
    {
      "host_path": "/host/thread/skills",
      "container_path": "/mnt/skills",
      "read_only": false
    }
  ]
}
```

The bundled provisioner translates this into a K8s `hostPath` volume with `DirectoryOrCreate`, and request-scoped mounts override default mount paths. That means a writable `/mnt/skills` extra mount replaces the default read-only public skills mount for the sandbox Pod. Custom provisioners can instead translate this into an `emptyDir`, PVC subPath, or equivalent writable volume. The important runtime check is inside the sandbox: `mkdir -p "$skills.container_path/custom"` and file writes through the AIO file API must succeed before DB skill materialization can be considered production-ready.

Example config payload:

```yaml
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  runtime_context_fail_closed: true
```

Changing this field requires a process restart because sandbox provider/runtime behavior is a startup/runtime boundary.

For a live Docker AIO smoke on an operator workstation that already has the sandbox image available locally:

```bash
DEER_FLOW_RUN_LIVE_AIO_SANDBOX=1 uv --directory backend run pytest tests/test_aio_sandbox_live.py -q
```

The smoke starts a real AIO sandbox container, mounts a temporary writable `/mnt/skills`, writes agent context under `/tmp/deerflow/context` and skill text/binary files under `/mnt/skills` through the sandbox file API, reads them back, and then destroys the container. It is opt-in so CI and routine local test runs do not start containers unexpectedly.

All live gates are also marked with pytest markers:

```bash
uv --directory backend run pytest -m live -q
uv --directory backend run pytest -m requires_llm -q
uv --directory backend run pytest -m docker_live -q
uv --directory backend run pytest -m remote_live -q
```

`-m live` collects model-backed client tests, selected real-LLM E2E tests, local Docker AIO smoke tests, remote provisioner/K8s smoke tests, Docker-backed lifecycle E2E tests, and other LLM-backed live tests. `-m requires_llm` narrows the run to model-backed gates. `-m docker_live` narrows the run to tests that require a local Docker daemon. `-m remote_live` narrows the run to tests that require a remote service or cluster. In an unconfigured workstation, these tests should collect and either skip cleanly or run only when their explicit local dependency is available.

Before running live gates, operators can machine-check the required environment and get the exact pytest commands:

```bash
uv --directory backend run python scripts/check_stateless_live_gates.py --json
uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --json
uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --run
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --json
uv --directory backend run python scripts/check_stateless_live_gates.py --gate requires_llm --run --evidence-path /tmp/deerflow-requires-llm-bundle/evidence.json --evidence-log-dir logs
uv --directory backend run python scripts/check_stateless_live_gates.py --gate mcp_stateless --json

# Validate an archived evidence file after a remote/model gate run.
uv --directory backend run python scripts/check_stateless_live_gates.py --validate-evidence /tmp/deerflow-requires-llm-bundle/evidence.json --require-run --require-logs --json
```

The preflight exits with code `0` only when selected gates have the required environment variables and config-source evidence to invoke their command. It exits with code `2` and reports `missing_env`, `invalid_env`, or `config_issues` when the command would only skip or be rejected by test preconditions. For `remote_live`, `invalid_env` includes malformed provisioner URLs, non-absolute host paths or prefixes, non-absolute container paths, and non-positive/non-integer ready timeouts. For `requires_llm`, file mode checks `DEER_FLOW_CONFIG_PATH` when it is set; otherwise it checks `DEER_FLOW_PROJECT_ROOT/config.yaml` when `DEER_FLOW_PROJECT_ROOT` is set; otherwise it checks the default project-root `config.yaml`. DB mode checks `DEER_FLOW_DATABASE_URL` and the `runtime_configs.app` payload using the same bootstrap DB URL parsing as gateway startup. For sqlite DB mode, the runtime DB file is `{sqlite_dir}/deerflow.db`, where `sqlite_dir` is derived from the URL path parent. `config_issues` includes missing `config.yaml`, missing `DEER_FLOW_CONFIG_PATH` file, invalid or missing `DEER_FLOW_PROJECT_ROOT`, missing DB URL, missing DB app config row, malformed YAML, an empty/invalid `models` list, or enabled stdio MCP servers that lack an explicit strict-stateless runtime mode. `mcp_stateless` accepts HTTP/SSE MCP servers and accepts enabled stdio MCP servers only when they declare `stateless.runtime_mode` as `sticky`, `sidecar`, or `single-node`; it records the same `mcp_compatibility` shape as migration dry-run. The model check recursively scans configured model entries for strings that start with `$`, such as `api_key: $OPENAI_API_KEY` or `api_key: $AZURE_OPENAI_API_KEY`, and reports absent variables in `missing_env`. Each gate report also includes `checked_env`, a sorted list of environment variable names checked by the preflight; this records names only, never values, so it can be archived without exposing credentials. Direct real-LLM pytest entrypoints, including `tests/test_client_live.py`, reuse this readiness contract and instantiate or preload models from the active file/DB AppConfig, so provider credentials are whatever the configured model references rather than a hard-coded `OPENAI_API_KEY` requirement.

When `--evidence-path` is provided, the script writes a JSON evidence report containing `schema_version`, `generated_at_utc`, `cwd`, git source metadata (`repo_root`, `head`, `branch`, `dirty`, and `status_short_count` when available), the preflight report including `checked_env`, selected gates, whether `--run` was requested, each executed command and exit code, and the overall exit code; keep that file with rollout records for remote/live model sign-off. When `--evidence-log-dir` is also provided during `--run`, captured command stdout/stderr are written to that directory and each execution entry records `stdout_log_path`, `stdout_log_sha256`, `stdout_log_bytes`, `stderr_log_path`, `stderr_log_sha256`, and `stderr_log_bytes`; archive that log directory with the JSON evidence. For portable rollout bundles, prefer an evidence path such as `<bundle>/evidence.json` and a relative log directory such as `--evidence-log-dir logs`; the command writes logs under `<bundle>/logs/` and records `logs/...` paths in the JSON. Absolute log directories remain supported, but those evidence records are environment-specific.

`--validate-evidence <file>` validates that archived JSON shape and returns `0` only when the evidence is valid and `overall_exit_code` is `0`; it returns `2` when the evidence is structurally valid but records a failed/skipped gate outcome, and `1` when the evidence file is malformed, has an unsupported schema, claims `overall_exit_code: 0` while an execution record has a non-zero `exit_code`, or fails strict log validation. Add `--require-run` for rollout sign-off so preflight-only or not-ready evidence is rejected unless `--run` was requested, `selected_gates` contains at least one known gate with no duplicates, `preflight.ok` is true, every selected gate has exactly one preflight entry with `ready_to_invoke: true`, every selected gate has exactly one execution record, no unselected gate has an execution record, and each execution command exactly matches that gate's preflight command. Add `--require-logs` for rollout sign-off when `--evidence-log-dir` was used, so validation also requires every execution to reference existing stdout/stderr log files whose SHA-256 and byte count match the archived evidence; relative log paths are resolved from the evidence JSON's directory, so portable bundles can be moved or unpacked and validated from any cwd. Manual prerequisites such as a reachable provisioner service, a writable Kubernetes node path, a running Docker daemon, or actually valid model provider credentials are still listed for operator confirmation because the script cannot prove those external conditions without actually running the live gate.

For a live remote provisioner/K8s smoke, run from an environment that can reach the provisioner service and knows a host path or host-path prefix that is valid on the Kubernetes node:

```bash
DEER_FLOW_RUN_REMOTE_AIO_SANDBOX=1 \
DEER_FLOW_REMOTE_AIO_PROVISIONER_URL=http://provisioner:8002 \
DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX=/var/lib/deerflow/runtime-skills \
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
```

Optional overrides:

- `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH` uses one exact node host path instead of appending the generated sandbox id to the prefix.
- `DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH` defaults to `/mnt/skills`.
- `DEER_FLOW_REMOTE_AIO_READY_TIMEOUT` defaults to `120` seconds.

The remote smoke creates a sandbox through `RemoteSandboxBackend`, sends the writable skills mount as `extra_mounts`, writes text and binary runtime-context files through the sandbox file API, verifies the prompt-exposed skills path from inside the sandbox, and destroys the sandbox. It proves the deployed provisioner/K8s mount mapping is writable by the AIO shell/file API user. It does not replace model-level live client tests.

The direct pytest entrypoint validates the same remote-live env shape as the preflight before it creates a sandbox, so invalid URLs, relative host/container paths, or invalid ready timeouts fail with an actionable message instead of surfacing later as a request or `ValueError` traceback.

The bundled provisioner records a mount-contract hash on each sandbox Pod. If `POST /api/sandboxes` finds an existing Pod for the same `sandbox_id` but the requested mount contract differs, it returns HTTP 409 instead of reusing the stale Pod. Destroy the stale sandbox and retry after confirming the requested `extra_mounts` point to the intended writable skills path.

`RemoteSandboxBackend` includes the provisioner response body in create-failure errors, so remote smoke output should preserve the HTTP 409 mount-contract detail.

## Post-Migration Checks

Run these checks before sending production traffic:

1. `GET /api/user-profile` returns the expected profile for the effective user.
2. `GET /api/default-agent-soul` returns the expected default-agent SOUL for the effective user.
3. `GET /api/agents` lists expected custom agents.
4. Custom skills can be listed/read and materialized into sandbox context.
5. A default-agent run materializes `agent/SOUL.md` into sandbox context when DB default SOUL exists.
6. MCP servers load from DB-backed config and remote HTTP/SSE servers connect successfully.
7. Memory update writes persist to DB and a fresh store instance can reload them.

For legacy global imports, remember that API calls authenticated as a real user do not automatically read owner `default`. Copy values from the compatibility owner to real users when needed.

## Default-Agent SOUL Admin Concurrency

`GET /api/default-agent-soul` returns:

- `content`
- `revision`

`PUT /api/default-agent-soul` accepts an optional `expected_revision` field. When present, the write succeeds only if the current revision still matches.

Example:

```json
{
  "content": "# Default Agent\n\nUse careful reasoning.",
  "expected_revision": 3
}
```

If another admin or process updates the default-agent SOUL first, the API returns `409` and leaves the current value unchanged. Omit `expected_revision` only when last-write-wins behavior is intentional.

## Rollback

V1 keeps file-mode compatibility. To roll back source selection:

1. Stop writer traffic.
2. Unset `DEER_FLOW_CONFIG_SOURCE=db`.
3. Restore or keep the original file-backed state directory.
4. Restart the gateway.

If any writes occurred in DB mode before rollback, decide whether to export those rows back to files or accept that the rollback uses the last file-backed snapshot.

## Known V1 Boundaries

- Public and custom skills are imported into normalized `skills` / `skill_files` rows; the legacy `custom_skills` JSON table remains as a compatibility/backfill source for custom skills.
- Large skill support files are blocked by preflight when they exceed `DEER_FLOW_DB_SKILL_FILE_MAX_BYTES`.
- Large skill packages are blocked by preflight when `SKILL.md` plus all support files exceed `DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES`.
- Memory recall and injection behavior remain behavior-compatible with file mode. Memory updater prompts use a bounded summary/top-facts snapshot; persisted DB memory remains complete.
- Stdio MCP runtime process state is not stored in DB. Strict stateless sign-off blocks enabled stdio MCP unless it is explicitly declared `sticky`, `sidecar`, or `single-node` compatible.
- Direct DB edits are not a supported operator workflow; use the API or migration command.
