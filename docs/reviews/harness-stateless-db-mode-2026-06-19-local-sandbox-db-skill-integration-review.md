# LocalSandbox DB Skill Integration Review

Date: 2026-06-19

Reviewed change set:

- DB-mode LocalSandbox integration test
- Real `DbSkillStorage` materialization through active AppConfig
- LocalSandbox read path for `/mnt/skills/custom/<skill>/SKILL.md`

## Findings

### P2 Fixed: LocalSandbox now has real DB-backed skill coverage

The previous sandbox mount review verified provider routing with mocked storage. This batch adds a real DB-mode integration test that writes a custom skill through `DbSkillStorage`, constructs `LocalSandboxProvider`, and reads the skill through the sandbox virtual path.

Coverage added:

- DB mode maps the default local skill storage class to `DbSkillStorage`.
- `DbSkillStorage` materializes the skill into the active cache root.
- `LocalSandboxProvider` maps `/mnt/skills` to that root.
- `LocalSandbox.read_file()` can read the DB-backed `SKILL.md`.

### P1: AIO/Docker end-to-end coverage is still missing

The local sandbox path is covered end to end. The AIO provider still has only mount-helper coverage because container-backed smoke tests need Docker/provisioner orchestration.

Required follow-up:

- Add a Docker-backed smoke test or provisioner-level contract test.
- Verify `DEER_FLOW_HOST_SKILLS_PATH` points at the DB materialized cache root in DB mode.

## Positive Checks

- The test uses real AppConfig, real SQLite DB config, real storage factory selection, and real LocalSandbox path resolution.
- The test resets AppConfig and skill storage singleton state after execution.
- No production code change was needed beyond the Batch 17 provider routing.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_local_sandbox_provider_mounts.py::TestLocalSandboxProviderMounts::test_local_sandbox_reads_db_materialized_custom_skill -q
```

Result:

```text
1 passed, 1 warning in 0.17s
```

## Review Conclusion

Local stateless execution now has an end-to-end guard that DB-backed skills are materialized and readable through the sandbox virtual mount. The only remaining sandbox-delivery gap is the containerized AIO/Docker path and its host-path translation contract.
