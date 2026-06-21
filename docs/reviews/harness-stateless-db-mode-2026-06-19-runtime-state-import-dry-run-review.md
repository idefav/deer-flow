# Runtime State Import Dry-run Review

Date: 2026-06-19

Reviewed change set:

- `scripts/import_runtime_state_to_db.py`
- runtime state inventory tests
- migration dry-run safety behavior

## Findings

### P1 Fixed: Migration now has a dry-run inventory entry point

The new script can enumerate file-backed runtime state without touching the database. It reports app config, extensions config, MCP servers, skill enabled state, agents, memory files, and public/custom skills.

Coverage added:

- config and extensions files.
- stdio and remote MCP servers.
- skill enabled state.
- legacy and per-user agents.
- global, user, and agent-scoped memory files.
- public and custom skills.

### P1 Fixed: MCP import risks are surfaced before apply

The dry-run report flags stdio MCP servers as stateful and flags resolved env/header/OAuth secrets that are not `$ENV_REF` values. This gives operators a chance to fix secrets before importing runtime state into DB-backed stores.

### P1: `--apply` is not implemented yet

The CLI exposes `--apply` as an explicit future mode, but currently raises `NotImplementedError`. This is intentional for this slice because dry-run should be safe and non-mutating.

Required follow-up:

- Implement apply for config, extensions, MCP, agents, memory, and skills.
- Keep dry-run as the default.
- Add per-resource apply summaries and conflict reporting.

### P2: Dry-run validates parse shape, not full schema yet

The script reports JSON/YAML parse errors and object-shape errors. It does not yet run full Pydantic validation for every target DB model.

Required follow-up:

- Validate app config with `AppConfig.from_payload()`.
- Validate extensions with `ExtensionsConfig`.
- Validate agents, memory, and skills using their existing loaders/scanners.

## Positive Checks

- Dry-run does not initialize SQLAlchemy stores and does not create a SQLite DB file.
- Existing user-isolation migration tests still pass.
- The report is JSON-compatible and suitable for CLI output or future API use.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_migration_user_isolation.py -q
```

Result:

```text
15 passed, 1 warning in 0.19s
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

The migration path now has a safe discovery phase. The stateless DB rollout still needs the mutating `--apply` path, but the operator-facing inventory and risk report are in place.
