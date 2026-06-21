# V1 Scope Decisions Review

Date: 2026-06-20

Reviewed artifacts:

- `docs/harness-stateless-db-mode-v1-scope-decisions.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `backend/packages/harness/deerflow/skills/storage/db_skill_storage.py`
- `backend/scripts/import_runtime_state_to_db.py`

## Findings

### P1 Fixed: Public-skill DB storage is no longer an ambiguous blocker

The audit previously listed public-skill DB storage as an unresolved product decision. Current implementation and migration behavior already had a consistent V1 boundary:

- custom skills are mutable runtime state and are stored in DB.
- public skills are bundled read-only platform artifacts.
- migration reports public skills with `file-backed-skip`.

The new V1 scope decision document records that boundary explicitly and states the operational contract: every V1 gateway/runtime image must include the expected public skill bundle, while AIO sandboxes receive enabled public/custom skill files through materialization.

### P1 Fixed: Normalized skill tables are a later schema migration, not a V1 completion blocker

The original technical design described normalized `skills`, `skill_files`, and `skill_history` tables. The implemented V1 path uses:

- `custom_skills.skill_md_text`
- `custom_skills.metadata_json`
- `custom_skills.files_json`
- `custom_skill_history.record_json`

This shape now supports V1 metadata-first custom skill listing, on-demand file reads, binary support files, mode metadata, and sandbox materialization. The decision document records when to reopen normalized tables: public skill DB seeding, large assets, per-file ACLs, per-file revisions, or cross-skill asset queries.

### P2 Remaining: Scope boundary must stay visible in rollout

This review does not claim public skills are DB-backed. It narrows the V1 claim to DB-backed mutable runtime state plus bundled public skill artifacts. Fully remote deployments that cannot guarantee the same public skill bundle in the gateway/runtime image still need a later seed/import path.

## Verification

Document-only review. The next verification batch should run `git diff --check` after the audit and decision docs settle.

## Conclusion

The public-skill and normalized-schema question is now a documented V1 scope boundary instead of an open blocker. The remaining implementation completion risks are remote provisioner/K8s writable skill-path proof and final broad verification.
