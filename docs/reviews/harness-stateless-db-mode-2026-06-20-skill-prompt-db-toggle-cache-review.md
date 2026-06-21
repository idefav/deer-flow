# Skill Prompt DB Toggle Cache Review

Date: 2026-06-20

Reviewed change set:

- real DB-backed enabled-skill prompt cache regression
- enabled-skill cache read-time revision check
- extensions-store ORM model registration for MCP table creation

## Findings

### P2 Fixed: DB skill disable no longer leaks through a stale prompt cache

`get_skills_prompt_section()` previously returned the in-process enabled-skill cache without first checking whether `runtime_configs.extensions` had advanced. A skill disabled by another DB writer could therefore remain advertised in the prompt until an explicit cache reset or warm-up path ran.

Batch 60 changes `get_cached_enabled_skills()` to call `_ensure_enabled_skills_cache()` before returning cached skills. When the DB extensions revision has changed, the helper clears the cached prompt section and enabled-skill list, starts a background refresh, and returns no stale skill entries for the current request.

### P2 Fixed: Real DB toggle coverage now exists

The new regression test uses:

- `DbExtensionsConfigStore` with SQLite.
- `DbSkillStorage` with a DB-backed custom skill.
- real `ExtensionsConfig` skill enabled state.
- `get_skills_prompt_section()` without an explicit cache reset after the DB update.

The test failed before the cache-read fix because `db-skill` was still present in the generated prompt after it was disabled in DB.

### P2 Fixed: Extensions-store table bootstrap is self-contained

`DbExtensionsConfigStore.save_extensions_config()` writes through the dedicated MCP store even when no MCP servers are present. In isolated usage, the store previously created DB metadata before the MCP ORM model had necessarily been imported, so `mcp_servers` could be missing.

The store now imports the ORM registration module before `Base.metadata.create_all()`, making extensions-store setup independent of unrelated test or app import order.

## Verification

Red test before the cache fix:

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py::test_skills_prompt_section_drops_stale_db_enabled_skill_after_revision_change -q
```

Result:

```text
FAILED ... assert 'db-skill' not in '<skill_system>...'
```

Green targeted test:

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py::test_skills_prompt_section_drops_stale_db_enabled_skill_after_revision_change -q
```

Result:

```text
1 passed, 1 warning in 0.31s
```

Related regression set:

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py tests/test_extensions_config_sources.py tests/test_skills_custom_router.py tests/test_db_skill_storage.py -q
```

Result:

```text
46 passed, 2 warnings in 1.00s
```

```bash
uv --directory backend run ruff check packages/harness/deerflow/agents/lead_agent/prompt.py packages/harness/deerflow/config/extensions_sources.py tests/test_lead_agent_prompt.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

## Conclusion

This closes the previously documented DB toggle integration gap for skill prompt cache freshness. The behavior remains non-blocking: a stale enabled skill is dropped immediately, while the refreshed list is loaded in the background.
