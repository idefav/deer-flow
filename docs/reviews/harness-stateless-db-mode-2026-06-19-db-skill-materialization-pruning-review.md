# DB Skill Materialization Pruning Review

Date: 2026-06-19

Reviewed change set:

- `ALLOWED_SUPPORT_SUBDIRS`
- `DbSkillStorage` materialized support-directory pruning
- stale custom skill directory pruning in `load_skills()`
- DB skill storage regression tests

## Findings

### P2 Fixed: Explicit materialization no longer preserves stale support files

`DbSkillStorage._materialize_row()` now removes allowed support subdirectories before writing the current DB-backed file set. A stale support file left in the cache no longer survives when the skill is materialized again.

Coverage added:

- Create a DB-backed skill with one support file.
- Add an extra support file only to the materialized cache.
- Rematerialize the skill.
- Assert the DB-backed file remains and the stale cache-only file is gone.

### P2 Fixed: Deleted custom skill cache directories are pruned during full load

`DbSkillStorage._iter_skill_files()` now prunes custom skill directories not represented by DB rows before yielding active skills. This prevents old custom skill directories from being parsed or mounted as if they were current DB state.

Coverage added:

- Create one DB-backed skill.
- Add a cache-only custom skill directory.
- Run `load_skills()`.
- Assert only the DB-backed skill is loaded and the cache-only directory is removed.

### P2: Cross-process freshness still needs explicit revision checks

This batch cleans dirty cache contents during materialization, but it does not notify other processes that they should rematerialize after a DB update.

Required follow-up:

- Add revision markers to materialized skill directories.
- Check DB `revision` before using a cached custom skill directory in long-lived processes.
- Add tests with two `DbSkillStorage` instances sharing DB and cache roots.

## Positive Checks

- The cleanup is scoped to DB-backed custom skills and allowed support directories.
- `.history` is preserved when pruning stale custom skill directories.
- Public skills remain filesystem-backed and are not touched by the DB custom cleanup.
- The new tests fail before the implementation and pass after it.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_db_skill_storage.py -q
```

Result:

```text
6 passed, 1 warning in 0.19s
```

## Review Conclusion

This batch closes the cache-staleness risk where old materialized skill files could leak into a stateless sandbox run. The remaining freshness gap is cross-process invalidation or revision-aware rematerialization, not ordinary stale files inside a materialized DB cache.
