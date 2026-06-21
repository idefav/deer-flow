# DB Skill Mode Metadata Review

Date: 2026-06-19

Reviewed change set:

- DB skill support-file mode metadata serialization
- archive mode extraction from `.skill` zip entries
- support-file materialization with restored permissions
- executable script regression test

## Findings

### P1 Fixed: DB-backed skill scripts can preserve executable bits

`DbSkillStorage` now reads Unix mode metadata from zip entries before extraction and stores that metadata with support-file payloads. During rematerialization it writes the file content first, then applies the stored mode with `chmod`.

Coverage added:

- `.skill` archive with `scripts/run.sh`.
- zip entry mode set to `0755`.
- install through DB storage.
- remove materialized cache.
- rematerialize from DB.
- assert executable bits survive.

### P2 Fixed: Support-file payloads stay backward compatible

Existing DB support files stored as plain strings still materialize as UTF-8 text. New metadata-bearing text payloads use `encoding: utf-8`, `text`, and `mode`; binary payloads continue to use `encoding: base64`, `data`, and optional `mode`.

This keeps existing rows readable while allowing scripts and binary files to carry metadata.

### P2: JSON payloads are still a pragmatic interim format

The current design avoids a schema split and keeps this batch small. It is acceptable for moderate support files, but large binary assets and richer metadata would be better represented in a dedicated table.

Required follow-up if asset size grows:

- Add a `custom_skill_files` table keyed by skill id and relative path.
- Store `content_bytes`, `content_text`, `encoding`, `mode`, hash, and size as first-class columns.

## Positive Checks

- The mode restoration path is only used when the DB payload carries explicit metadata.
- Plain text support files remain unchanged.
- Binary support-file behavior from the previous batch remains covered by the same focused suite.
- Rewrites of an existing metadata-bearing support file preserve the stored mode when no new mode is supplied.

## Verification Evidence

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

## Review Conclusion

The DB skill storage path now preserves both binary bytes and executable mode metadata for archive-installed support files. The remaining concern is storage shape and scalability, not correctness for the current stateless materialization flow.
