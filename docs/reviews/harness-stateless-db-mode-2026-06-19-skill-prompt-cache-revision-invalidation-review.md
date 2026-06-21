# Skill Prompt Cache Revision Invalidation Review

Date: 2026-06-19

Reviewed change set:

- enabled-skill prompt cache revision tracking
- `_ensure_enabled_skills_cache()` revision invalidation
- lead-agent prompt cache regression test

## Findings

### P1 Fixed: Skill prompt cache can refresh after DB extensions revision changes

The enabled-skills cache now records the extensions revision used to load the skill list. When the current revision differs, `_ensure_enabled_skills_cache()` invalidates cached skill prompt state and starts a refresh.

Coverage added:

- Warm enabled skills at revision 1.
- Change revision and storage output.
- Warm again.
- Assert the cache returns the new skill list.

### P2 Fixed In Batch 60: True DB toggle integration test covers prompt cache freshness

The original test mocked revision and storage output to isolate cache logic. Batch 60 added a real SQLite-backed regression that seeds `DbSkillStorage`, writes `runtime_configs.extensions.skills`, warms the prompt cache, then disables the skill through `DbExtensionsConfigStore`.

The test initially failed because `get_skills_prompt_section()` returned the stale cached enabled skill after the DB revision changed. The runtime now checks the extensions revision before returning the enabled-skills cache, drops stale prompt entries, and starts the background refresh instead of continuing to advertise a disabled DB-backed skill.

Batch 60 also fixed `DbExtensionsConfigStore` table bootstrap so isolated extensions-store usage registers the MCP ORM table before creating DB metadata.

## Positive Checks

- Explicit `refresh_skills_system_prompt_cache_async()` still works.
- Worker coalescing still avoids parallel refresh workers.
- Explicit app_config skill cache remains keyed by config identity.
- Prompt section LRU cache is cleared when revision changes.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_lead_agent_prompt.py tests/test_lead_agent_skills.py tests/test_skills_loader.py tests/test_skills_custom_router.py tests/test_extensions_config_sources.py -q
```

Result:

```text
53 passed, 2 warnings in 0.70s
```

## Review Conclusion

Skill prompt cache now has DB revision-aware invalidation and real DB toggle coverage. Remaining work is performance tuning for revision checks if prompt construction becomes hot enough to justify a lower-latency invalidation channel.
