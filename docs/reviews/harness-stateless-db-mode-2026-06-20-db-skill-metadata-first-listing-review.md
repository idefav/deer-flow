# DB Skill Metadata-First Listing Review

Date: 2026-06-20

Reviewed change set:

- `custom_skills.metadata_json`
- DB custom skill metadata extraction on `SKILL.md` writes
- `DbSkillStorage.load_skills()` metadata-first custom listing
- old `custom_skills` table compatibility/backfill

## Findings

### P1 Fixed: DB custom skill listing no longer reparses materialized `SKILL.md`

Before this batch, `DbSkillStorage` inherited the base `SkillStorage.load_skills()`, which discovered DB-backed custom skills through a materialized `SKILL.md` path and then called `parse_skill_file()` on that file. Batch 62 had stopped eager support-file materialization, but listing still needed the main file contents.

Batch 63 stores custom skill metadata in `custom_skills.metadata_json` when `SKILL.md` is written or installed. `DbSkillStorage.load_skills()` now builds custom `Skill` objects from that DB metadata while keeping public skills on the existing filesystem parser path.

Regression coverage:

- write a DB custom skill with description, license, and `allowed-tools`.
- corrupt the materialized cache copy of `SKILL.md`.
- monkeypatch `deerflow.skills.parser.parse_skill_file` to raise.
- call `load_skills()`.
- assert metadata is returned from DB and the cache copy is refreshed from DB.

### P1 Fixed: Existing prototype tables can be opened after the metadata column is introduced

Adding an ORM column without a migration would break existing local DBs whose `custom_skills` table was created before this batch. The storage constructor now checks the physical table and adds `metadata_json` when it is missing.

Regression coverage creates the old table shape manually, inserts a skill row, constructs `DbSkillStorage`, and verifies listing works and the column exists afterward.

### P2 Remaining: Schema is still V1-specific, not the normalized design

The original technical design described normalized `skills`, `skill_files`, and `skill_history` tables. The implemented V1 path keeps the existing prototype shape:

- `custom_skills.skill_md_text`
- `custom_skills.metadata_json`
- `custom_skills.files_json`
- `custom_skill_history`

This is now enough for custom skill metadata-first listing and on-demand file access, but it should stay documented as a design divergence if later requirements need cross-skill indexing, per-file ACLs, or public skill DB storage.

### P2 Remaining: Public skills remain filesystem-backed

`DbSkillStorage.load_skills()` intentionally still parses public skills from the filesystem. This matches the current V1 boundary and import behavior, but it does not satisfy a future requirement where all public/bundled skill content must also live in DB.

## Verification

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

## Conclusion

The V1 custom-skill metadata-first listing requirement is closed. Remaining skill work is no longer about reparsing custom `SKILL.md` during listing; it is about product scope and schema shape: normalized skill tables, public skill DB storage, and broader end-to-end DB-mode run evidence.
