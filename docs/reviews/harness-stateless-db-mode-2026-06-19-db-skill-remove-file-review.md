# DB Skill Remove-file Review

Date: 2026-06-19

Reviewed change set:

- `SkillStorage.delete_custom_skill_file()`
- `DbSkillStorage.delete_custom_skill_file()`
- `skill_manage remove_file` storage routing
- DB storage and tool-level regression tests

## Findings

### P1 Fixed: Support-file delete no longer bypasses DB storage

The previous review found that `skill_manage remove_file` only unlinked the materialized cache path, leaving `custom_skills.files_json` unchanged. This batch routes removal through the storage abstraction and gives `DbSkillStorage` its own DB update path.

Coverage added:

- Direct DB storage deletion test verifies that a removed support file does not reappear after deleting and rematerializing the cache.
- Tool-level test verifies that `skill_manage remove_file` records previous content in history while removing the DB backing record.

### P2: Materialization still does not prune stale files globally

This fix unlinks the removed target during the explicit delete operation. A cache directory that already contains stale files from another process or older revision can still retain files that are absent from DB because `_materialize_row()` writes current files but does not clean unknown files.

Required follow-up:

- Add a revision-aware materialization manifest, or prune support subdirectories before writing the current DB file set.
- Include concurrency tests with two `DbSkillStorage` instances using the same DB and cache root.

### P2: DB support files remain text-only

The delete path is correct for the current text support-file representation. It does not address the broader binary asset limitation from the DB skill storage review.

Required follow-up:

- Introduce typed support-file storage for binary assets and scripts.
- Preserve content type and executable metadata during install, materialization, and deletion.

## Positive Checks

- Local filesystem storage keeps the original behavior through the default base implementation.
- DB storage now increments `revision` when support files are removed.
- Existing history behavior is preserved: `remove_file` stores the deleted file content before deletion.
- The new tests fail before the implementation and pass after it.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skill_manage_tool.py -q
```

Result:

```text
10 passed, 1 warning in 0.32s
```

## Review Conclusion

The P1 DB-source-of-truth bug for explicit support-file deletion is closed. Remaining risk is now in cache lifecycle and richer asset storage, not in the `skill_manage remove_file` operation itself.
