# Harness Stateless DB Mode V1 Scope Decisions

Date: 2026-06-20

This document records product and schema boundaries that were left open during implementation. It should be read with:

- `docs/harness-stateless-db-mode-plan.md`
- `docs/harness-stateless-db-mode-technical-design.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-operator-runbook.md`

## Decision 1: Public Skills Are DB-Seeded In Strict Stateless Mode

Status: supersedes the earlier 2026-06-20 V1 boundary that treated public skills as deployment artifacts.

Strict stateless DB mode stores both public and custom skills in DB. Public skills remain platform-owned/read-only content from a product perspective, but they are seeded/imported into normalized DB rows before runtime so the gateway no longer depends on a bundled filesystem copy while running in DB mode.

Rationale:

- "Complete stateless" requires public skills to be available from DB, not only from a deployment artifact.
- `DbSkillStorage` now fails closed for unseeded public skills in DB mode instead of falling back to `skills/public` on the gateway filesystem.
- The import command reports public and custom skills with `import_action: import-to-db`, `storage_boundary: db`, `runtime_artifact_required: false`, and `deployment_artifact: null`.
- Runtime sandbox delivery still materializes only the files needed for the run into `skills.container_path`; DB remains the source of truth.

Operational contract:

- Seed/import the public skill bundle into DB during migration or release rollout.
- Treat missing public DB seed data as a production sign-off blocker.
- AIO sandboxes do not need public or custom skill bundles preinstalled; enabled skill files are materialized to `skills.container_path` before sandbox tools read them.

Out of scope for V1:

- Per-tenant public skill overrides.
- Public skill version rollout and rollback through DB.

## Decision 2: Normalized Skill Tables Are The DB-Mode Read Path

Status: supersedes the earlier 2026-06-20 V1 boundary that deferred normalized tables.

V1 now creates and reads:

- `skills`
- `skill_files`
- `custom_skill_history`

The legacy custom-skill table remains for compatibility/backfill:

- `custom_skills.skill_md_text`
- `custom_skills.metadata_json`
- `custom_skills.files_json`

Rationale:

- Public skills need DB seed rows, so a category-aware normalized table is required.
- `load_skills()` can list metadata from `skills.metadata_json` without reading support files.
- `list_skill_file_manifest()` and `read_skill_file()` can read single DB file rows for progressive loading and sandbox materialization.
- Dual-write/backfill preserves compatibility for existing custom-skill data.

Known limits:

- Per-file ACLs and public skill version rollout are not implemented.
- Stdio MCP process state is still an operational compatibility boundary, not a DB schema feature.

Later migration path:

1. Keep dual-write until existing deployments have backfilled custom skills.
2. Add public skill version/revision rollout controls if platform-owned skills need staged DB releases.
3. Remove the legacy `custom_skills` compatibility dependency after an explicit migration window.

## Decision 3: Remote Provisioner/K8s AIO Writable Skill Path Is A Rollout Gate

Batch 67 verifies local Docker AIO with:

- agent/memory context under `/tmp/deerflow/context`.
- skill files under `skills.container_path`, default `/mnt/skills`.
- a per-thread writable scratch mount for local Docker DB mode.

Remote provisioner/K8s deployments must provide the same writable path contract.

Required for production rollout:

- `skills.container_path` exists and is writable by the AIO shell/file API user, or
- `skills.container_path` is configured to a path that is already writable, such as `/tmp/deerflow/skills`.

This is a deployment proof gate, not a code-path gap in local Docker AIO.
