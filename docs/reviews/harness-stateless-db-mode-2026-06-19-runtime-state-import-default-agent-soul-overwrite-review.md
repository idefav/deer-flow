# Runtime State Import Default Agent SOUL Overwrite Review

Date: 2026-06-19

## Scope

- default-agent SOUL overwrite semantics in `scripts/import_runtime_state_to_db.py`
- `tests/test_import_runtime_state_to_db.py`
- implementation log Batch 55

## Findings

### P2 Fixed: Default-agent SOUL overwrite now has focused regression coverage

The production behavior was already implemented by the Batch 54 path:

- preflight detects existing `default_agent_souls` rows as `default_agent_soul` conflicts.
- `--overwrite` turns those conflicts into non-blocking conflicts.
- apply writes through `DbAgentStore.save_default_agent_soul(...)`, which upserts and increments revision.

Batch 55 adds a dedicated regression test so this is no longer only implied by the generic overwrite mechanism.

### P2 Fixed: Replacement reporting includes resource-specific accounting

The new test asserts:

- `preflight.conflicts == [{"resource": "default_agent_soul", "owner_user_id": "default"}]`
- `applied.overwritten_conflicts == 1`
- `applied.overwritten_by_resource == {"default_agent_soul": 1}`

This gives migration operators the same overwrite visibility for default-agent SOUL as for user profiles, agents, memory, and skills.

### P2 Fixed: Revision advancement is verified

The test reads the `DefaultAgentSoulRow` after apply and verifies `revision == 2`. This protects the row-level freshness contract from accidental future changes that replace content without advancing revision.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_overwrite_replaces_default_agent_soul -q
```

Result:

- targeted default-agent SOUL overwrite test: `1 passed, 1 warning`

## Conclusion

Default-agent SOUL overwrite behavior is now explicitly covered. No production code change was needed in this batch because the existing DB conflict and upsert path already satisfied the intended behavior.
