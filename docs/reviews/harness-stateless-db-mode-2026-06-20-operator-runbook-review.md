# Operator Runbook Review

Date: 2026-06-20

## Scope

- `docs/harness-stateless-db-mode-operator-runbook.md`
- implementation log Batch 57

## Findings

### P2 Fixed: Legacy global owner mapping is now operator-visible

The runbook documents how legacy global files map into DB rows:

- `USER.md` -> `user_profiles.owner_user_id = "default"`
- `SOUL.md` -> `default_agent_souls.owner_user_id = "default"`
- global memory -> owner `default`, global memory scope
- legacy shared agents -> owner `default`
- legacy shared agent memory -> owner `default`, named agent scope

It also states that `default` is a compatibility owner rather than a real user account.

### P2 Fixed: Migration commands are now documented as an operator flow

The runbook includes dry-run, apply, and apply-with-overwrite examples using `--database-url`. It calls out the preflight fields operators must review before apply.

### P2 Fixed: Post-migration checks include default-agent SOUL

The runbook includes `GET /api/default-agent-soul` in post-migration checks, aligning with the Batch 56 API surface and making the default-agent identity a first-class verification point.

### P3 Fixed In Batch 59: Revision-aware default-agent SOUL admin workflow is documented

Batch 59 adds `revision` responses and optional `expected_revision` conditional writes to `GET/PUT /api/default-agent-soul`. The runbook now documents the optimistic edit workflow and 409 conflict behavior.

## Verification

```bash
git diff --check
```

Result:

- `git diff --check` produced no output.

## Conclusion

The migration path now has an operator-facing runbook that explains how to execute V1 DB import safely and how to interpret the `default` compatibility owner.
