# Runtime State Import Agent Apply Review

Date: 2026-06-19

Reviewed change set:

- user profile inventory
- agent/profile apply path in `scripts/import_runtime_state_to_db.py`
- migration apply tests for `DbAgentStore`

## Findings

### P1 Fixed: Migration apply can import user profiles and custom agents

The import script now writes global `USER.md` into `DbAgentStore` as owner `default`, imports legacy shared agents as owner `default`, and imports per-user agents under their user directory name.

Coverage added:

- `USER.md` inventory and apply count.
- legacy agent `config.yaml` and `SOUL.md`.
- per-user agent `config.yaml` and `SOUL.md`.
- DB reads through `DbAgentStore` after apply.

### P1: Duplicate/conflict policy is still simple overwrite

`DbAgentStore.save_agent()` upserts by owner and agent name. If an import source contains duplicate owner/name entries, later saves can overwrite earlier content.

Required follow-up:

- Detect duplicate owner/name in dry-run.
- Require an explicit overwrite flag or report conflict before apply.

### P2: Agent validation errors are apply-time only

Invalid agent YAML is raised during apply. Dry-run currently reports inventory presence but does not validate every agent config.

Required follow-up:

- Validate agent config during dry-run.
- Include per-agent validation errors in the report.

## Positive Checks

- The migration path reuses `DbAgentStore` instead of writing ORM rows directly.
- Existing custom-agent API and tool tests remain green.
- Dry-run still does not create DB files.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_db_agent_store.py tests/test_custom_agent.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py -q
```

Result:

```text
106 passed, 2 warnings in 0.90s
```

```bash
uv --directory backend run ruff check tests/test_import_runtime_state_to_db.py scripts/import_runtime_state_to_db.py
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

## Review Conclusion

The migration command can now apply config, extensions/MCP, profiles, and agents. Remaining apply work is memory and skills, plus stronger preflight validation and conflict reporting.
