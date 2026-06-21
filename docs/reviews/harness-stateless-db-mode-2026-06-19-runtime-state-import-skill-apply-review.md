# Runtime State Import Skill Apply Review

Date: 2026-06-19

Reviewed change set:

- custom skill apply path in `scripts/import_runtime_state_to_db.py`
- skill import regression test

## Findings

### P1 Fixed: Migration apply can import custom skills into DB storage

The import script now writes discovered custom skills into `DbSkillStorage`, including `SKILL.md` and support files. Support files are imported as text when UTF-8 decodable, otherwise as bytes, and mode metadata is preserved.

Coverage added:

- custom skill import.
- support-file rematerialization from DB.
- public skill remains outside custom DB import.

### P1 Fixed: Skill import no longer mutates the source skills directory

An initial implementation used the source `skills_root` as the DB materialization root. That is unsafe because `DbSkillStorage` prunes support directories to make the cache match DB state. The implementation now uses a temporary materialization root so import reads from source and writes only to DB.

### P1: Existing DB custom skill conflict policy is still overwrite/update

`DbSkillStorage.write_custom_skill()` updates existing custom skill rows. The migration script does not yet preflight whether a DB skill already exists.

Required follow-up:

- Detect existing DB custom skills during dry-run.
- Add explicit overwrite/skip policy.

### P2: Public skill migration remains a product decision

Public skills are skipped by apply and remain file-backed/read-only for V1. Fully remote stateless deployments may eventually need seeded public skills or bundled immutable assets.

Required follow-up:

- Decide whether public skills are deployment artifacts or DB-seeded resources.

## Positive Checks

- The migration reuses `DbSkillStorage` serialization/materialization logic.
- Binary/mode support from previous skill batches is reused for migration support files.
- Existing skill manager, installer, loader, and custom router tests remain green.

## Verification Evidence

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

## Review Conclusion

The migration command can now apply all V1 user-editable runtime state categories: config, extensions/MCP, profiles, agents, memory, and custom skills. Remaining work is preflight quality, conflict policy, and the public-skill deployment decision.
