# DB Skill Binary Asset Storage Review

Date: 2026-06-19

Reviewed change set:

- DB skill support-file binary serialization
- DB skill archive installation path
- DB skill materialization of base64 support payloads
- binary asset regression test

## Findings

### P1 Fixed: DB-backed skill archives can preserve binary support assets

`DbSkillStorage` no longer assumes every support file is UTF-8 text. During archive installation it reads support files as bytes, stores UTF-8 files as strings, and stores non-UTF-8 files as base64 JSON payloads. Materialization writes those payloads back as bytes.

Coverage added:

- `.skill` archive with `assets/logo.bin`.
- Install through DB storage.
- Delete materialized cache.
- Rematerialize from DB.
- Assert byte-for-byte asset preservation.

### P2: Script executable metadata is still not preserved

Binary payload preservation solves asset bytes, but archive permission bits are still not represented in DB. Script support files may rematerialize without executable mode metadata.

Required follow-up:

- Store support file metadata alongside payload: mode, maybe content type.
- Apply executable/readable permissions during materialization.

### P2: Large binary payloads in JSON may not be ideal long term

The current JSON payload keeps the schema small and backward compatible, but large assets would bloat `custom_skills.files_json`.

Required follow-up:

- Consider a dedicated `custom_skill_files` table with typed blob/text columns if assets grow.

## Positive Checks

- Existing plain-text support files remain stored as strings.
- Existing materialization, delete-file, pruning, and skill-manager tests still pass.
- Local file-backed storage behavior is unchanged.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skill_manage_tool.py tests/test_skills_installer.py tests/test_skills_archive_root.py tests/test_skills_loader.py tests/test_skills_custom_router.py -q
```

Result:

```text
63 passed, 2 warnings in 0.78s
```

## Review Conclusion

The text-only P1 in DB skill storage is closed for binary asset bytes. The remaining DB skill asset work is metadata fidelity and long-term storage shape, not byte preservation.
