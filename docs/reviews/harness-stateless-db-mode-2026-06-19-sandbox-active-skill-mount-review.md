# Sandbox Active Skill Mount Review

Date: 2026-06-19

Reviewed change set:

- `LocalSandboxProvider` skills path mapping
- `AioSandboxProvider` skills mount helper
- sandbox provider regression tests for active skill storage root

## Findings

### P2 Fixed: Sandbox skill mounts no longer infer source root only from config

Both LocalSandbox and AIO sandbox providers now call `get_or_new_skill_storage(app_config=config).get_skills_root_path()` when computing the host-side skills mount. In DB mode, this lets the storage layer materialize DB-backed custom skills and expose the materialized root to sandbox.

Coverage added:

- Local provider test patches active storage root and verifies `/mnt/skills` maps to that root.
- AIO provider test patches active storage root and verifies `_get_skills_mount()` returns that root as the read-only mount source.

### P1: Docker host-path translation still needs a production contract

`AioSandboxProvider` still supports `DEER_FLOW_HOST_SKILLS_PATH`, which overrides the mount source for Docker-in-Docker host resolution. That is necessary for existing deployments, but DB mode now needs an explicit deployment rule: the host override must point at the same materialized skills cache root visible to the gateway process.

Required follow-up:

- Document DB-mode Docker deployment requirements for materialized skill cache bind mounts.
- Add an integration test or smoke test that sets `DEER_FLOW_HOST_SKILLS_PATH` to the materialized cache root and verifies the sandbox sees DB-backed skills.

### P2: Real DB storage integration test is still missing

The new tests verify provider routing through the storage abstraction. They do not construct a real `DbSkillStorage` through AppConfig and assert end-to-end skill readability inside LocalSandbox.

Required follow-up:

- Create a DB-backed custom skill.
- Instantiate LocalSandboxProvider in DB config mode.
- Read `/mnt/skills/custom/<name>/SKILL.md` through the acquired sandbox.

## Positive Checks

- Existing container path behavior remains config-driven.
- Skill mounts stay read-only.
- The implementation is shared through the existing storage factory rather than adding sandbox-specific DB logic.
- The new tests fail before implementation and pass after it.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_local_sandbox_provider_mounts.py::TestLocalSandboxProviderMounts::test_setup_path_mappings_uses_active_skill_storage_root tests/test_aio_sandbox_provider.py::test_get_skills_mount_uses_active_skill_storage_root -q
```

Result:

```text
2 passed, 1 warning in 0.16s
```

## Review Conclusion

The sandbox providers are now aligned with the DB skill storage abstraction for mount source selection. The remaining production risk is host-path translation in Docker deployments, where the materialized cache root must be mounted consistently for both gateway and sandbox container creation.
