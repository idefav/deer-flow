# DB Skill Storage Review

Date: 2026-06-19

Reviewed change set:

- `custom_skills` and `custom_skill_history` ORM models
- `DbSkillStorage`
- skill storage factory DB-mode default switch
- DB skill storage tests
- skill storage/router/tool regression subset

## Findings

### P1: Support-file delete still bypasses DB storage

`skill_manage` remove-file currently resolves a filesystem path and unlinks it directly. With DB storage, that removes the materialized cache file but does not remove the file from `files_json`.

Required follow-up:

- Add `delete_custom_skill_file(name, relative_path)` to `SkillStorage`.
- Route `skill_manage` remove-file through the storage method.

### P1: Binary assets are not supported

`DbSkillStorage` stores support files as UTF-8 text in `files_json`. Existing skill archives may include binary assets.

Required follow-up:

- Store support files as typed blobs, or split text/blob rows.
- Preserve executable metadata for scripts.

### P2: Sandbox providers still read the configured skills path directly

The DB storage materializes into the configured skills path, so existing mounts work when factory and config agree. The sandbox providers still do not ask the active storage for its root.

Required follow-up:

- Update Local and AIO sandbox providers to call `get_or_new_skill_storage(app_config=config).get_skills_root_path()`.
- Add tests that DB materialized skills are mounted into sandbox.

### P2: Materialized cache lifecycle is implicit

The cache is recreated on load, but cleanup policy, stale-file pruning, and multi-process coordination are not defined.

Required follow-up:

- Add materialization revision markers.
- Prune stale custom skill directories before materializing DB rows.

## Positive Checks

- DB custom skills survive materialized cache deletion.
- Existing skill parser, router, and skill-manage tests still pass.
- Factory auto-switches only in DB config mode and only for the default local storage class.
- Public skills remain file-backed for compatibility.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py tests/test_skills_loader.py tests/test_skills_custom_router.py tests/test_skill_manage_tool.py tests/test_skills_parser.py -q
uv --directory backend run ruff check tests/test_db_skill_storage.py packages/harness/deerflow/skills/storage/db_skill_storage.py packages/harness/deerflow/skills/storage/__init__.py packages/harness/deerflow/persistence/skills packages/harness/deerflow/persistence/models/__init__.py
```

Result:

```text
44 passed, 2 warnings in 0.68s
All checks passed!
```

## Review Conclusion

This batch establishes DB as the source of truth for custom skill `SKILL.md`, support text files, and history while keeping the current Path-based runtime contract working through materialization. The remaining work is mostly around asset completeness, cache lifecycle, and ensuring all file operations go through the storage abstraction.
