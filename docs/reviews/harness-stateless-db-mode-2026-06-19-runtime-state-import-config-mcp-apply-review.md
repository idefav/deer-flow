# Runtime State Import Config/MCP Apply Review

Date: 2026-06-19

Reviewed change set:

- config/extensions/MCP apply path in `scripts/import_runtime_state_to_db.py`
- runtime-config sync upsert helper
- apply regression test

## Findings

### P1 Fixed: Migration can now apply app config and extensions/MCP state

`import_runtime_state_to_db(..., apply=True)` now writes `config.yaml` into `runtime_configs.app` and writes `extensions_config.json` through `DbExtensionsConfigStore`.

Because the extensions store already splits MCP servers into `mcp_servers`, apply imports MCP server config into the first-class MCP table while keeping skill enabled state in `runtime_configs.extensions`.

Coverage added:

- `runtime_configs.app` receives the app config payload.
- `runtime_configs.extensions` receives skill state only.
- `mcp_servers` receives MCP server env refs.
- `updated_by` is recorded on the app config row.

### P1: Apply is intentionally partial

This batch does not import agents, user profiles, memory, or skills. The dry-run inventory reports them, but apply only mutates config/extensions/MCP.

Required follow-up:

- Import custom agents and `USER.md` via `DbAgentStore`.
- Import memory files via `DbMemoryStorage`.
- Import custom skills via `DbSkillStorage`.

### P2: Validation is still mostly parser-level

The extensions apply path validates with `ExtensionsConfig`, but app config currently writes the raw YAML payload. Full `AppConfig.from_payload()` validation should be added before production use of `--apply`.

Required follow-up:

- Validate app config before write.
- Include validation errors in dry-run output so operators can fix them before apply.

## Positive Checks

- Dry-run remains non-mutating.
- Config/MCP apply reuses the existing DB stores rather than duplicating write logic.
- Existing runtime config, extensions, MCP, and secret preservation tests remain green.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_runtime_config_store.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_mcp_config_secrets.py -q
```

Result:

```text
50 passed, 1 warning in 0.77s
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

The migration command now has a real apply path for config/extensions/MCP. The remaining migration work is to import user-owned runtime data: agents, memory, and skills.
