# DB Skill Toggle Review

Date: 2026-06-19

Reviewed change set:

- DB-mode `/api/skills/{skill_name}` update path
- DB-mode router test for preserving MCP/extras while updating skill state
- related skills/MCP regression subset

## Findings

### P2: Extensions payload update logic is duplicated

The MCP router and skills router now both rebuild an extensions payload with MCP servers, skills, and model extras.

Required follow-up:

- Extract a shared update helper around `DbExtensionsConfigStore`.
- Add revision checks in the helper.

### P2: Skill prompt cache reset remains process-local

`refresh_skills_system_prompt_cache_async()` updates only the current process. In multi-worker DB mode, other processes can keep old enabled-skill state.

Required follow-up:

- Add extensions revision checks to skill prompt cache keys.
- Add multi-process invalidation or short TTL.

## Positive Checks

- DB mode no longer resolves or writes `extensions_config.json` for skill enabled toggles.
- Existing file-mode skill update test still passes.
- MCP server config and top-level `mcpInterceptors` are preserved when a skill is toggled.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_skills_custom_router.py tests/test_mcp_config_secrets.py tests/test_extensions_config_sources.py tests/test_db_skill_storage.py -q
uv --directory backend run ruff check tests/test_skills_custom_router.py app/gateway/routers/skills.py
```

Result:

```text
45 passed, 2 warnings in 0.68s
All checks passed!
```

## Review Conclusion

This batch closes the obvious extensions-config write gap left after MCP DB routing: skill enabled state now uses DB storage in DB mode. The remaining concerns are shared update abstraction and cross-process cache freshness.
