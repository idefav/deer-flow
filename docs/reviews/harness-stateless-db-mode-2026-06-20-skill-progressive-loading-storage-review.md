# Skill Progressive Loading Storage Review

Date: 2026-06-20

Reviewed change set:

- `SkillFileManifest`
- `SkillStorage.read_skill_file()`
- `SkillStorage.list_skill_file_manifest()`
- DB-backed overrides for custom skill file reads and manifests
- DB skill listing that materializes only `SKILL.md`

## Findings

### P1 Fixed: DB skill listing no longer materializes full support-file trees

Before this batch, `DbSkillStorage.load_skills()` reached `_iter_skill_files()`, which called `_materialize_row()` for each DB row. That wrote `SKILL.md` plus every support file under `references/`, `templates/`, `scripts/`, and `assets/`.

Batch 62 changes listing to materialize only `SKILL.md`. Support files are now left in DB until a caller explicitly asks for the full custom skill directory or reads a specific file through the storage API.

Regression coverage:

- write a DB custom skill plus `references/notes.md`.
- delete the materialized cache.
- call `load_skills()`.
- assert `SKILL.md` exists but `references/notes.md` was not materialized.

### P1 Fixed: Storage API can read skill files and list file manifests

`SkillStorage` now exposes:

- `read_skill_file(skill_name, category, relative_path)`
- `list_skill_file_manifest(skill_name, category)`

The local filesystem implementation uses the base methods. `DbSkillStorage` overrides custom-skill reads and manifests so callers can inspect DB-backed files without requiring the materialized support tree.

Regression coverage:

- list manifest for a DB-backed custom skill.
- assert `SKILL.md` and `references/notes.md` entries are reported.
- read `references/notes.md` through DB storage.
- assert the support file still was not materialized into the cache.

### P2 Remaining: True metadata-only listing is still not complete

This batch reduces eager materialization, but `load_skills()` still parses `SKILL.md` to obtain metadata. The technical design's stronger target is a metadata-first list path backed by stored metadata, then `SKILL.md` read on demand.

The requirement audit remains correct to keep the metadata-only acceptance criterion as partial.

## Verification

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
```

Result:

```text
All checks passed.
```

## Conclusion

The support-file eager materialization part of the progressive-loading gap is closed. The remaining work is metadata-first listing: storing or deriving skill metadata without parsing/materializing `SKILL.md` during list/prompt paths.
