# Review: Normalized Public Skill DB Storage

Date: 2026-06-21

## Scope

Reviewed the close-out change that makes `skills` / `skill_files` the DB-mode read path for both public and custom skills.

## Findings

- No blocking issues found in the implemented scope.
- Public skills can now be seeded/imported into DB and read after the source filesystem tree is removed.
- DB mode fails closed when a public skill exists only in the gateway filesystem cache and has not been seeded into DB.
- Legacy `custom_skills` rows are backfilled into normalized rows, and custom writes still maintain the compatibility table while normalized rows become the runtime source.
- Progressive loading is preserved: listing reads metadata and only materializes `SKILL.md`; support files and binary assets remain on-demand through manifest/read APIs.

## Residual Risk

- Public skill version rollout and rollback are not implemented as a product workflow.
- The legacy `custom_skills` compatibility table still needs to remain until deployed databases are backfilled.

## Verification Reviewed

```text
Red: 3 failed, 1 warning in 0.24s
Focused storage tests: 3 passed, 1 warning in 0.20s
Focused import tests: 3 passed, 1 warning in 0.45s
```
