# Runtime State Import Extensions Preflight Review

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- extensions/MCP validation in runtime-state import preflight
- `tests/test_import_runtime_state_to_db.py`
- implementation log Batch 44

## Findings

### P2 Fixed: Dry-run now validates extensions/MCP semantics

The import command previously checked whether `extensions_config.json` was syntactically valid JSON but did not validate the payload with `ExtensionsConfig`. A malformed-but-parseable extensions file could pass dry-run and fail later during apply or runtime load.

Preflight now validates with `ExtensionsConfig.model_validate(ExtensionsConfig.resolve_env_variables(payload))` and reports failures as `extensions_config` source errors.

### P2 Fixed: Dry-run remains non-mutating on extensions validation failure

The regression test uses `mcpServers: []`, which is valid JSON but invalid for the configured schema. It verifies that:

- `preflight.ok` is false.
- the error is attached to `extensions_config`.
- the sqlite DB file is not created.

This preserves the migration command's safety contract for optional extensions files.

### P2: Validation follows the existing file-loading semantics

The preflight path resolves environment placeholders with `ExtensionsConfig.resolve_env_variables()` before model validation, matching `ExtensionsConfig.from_file()`.

This matters for MCP secrets because unresolved `$VAR` placeholders are normalized the same way during preflight as they are during runtime file loading.

### P3: Error payload is still human-readable text

As with app config and agent validation, the preflight report stores the exception string. This is enough for migration operator review, but not yet ideal for automated remediation.

Potential future improvement:

- Preserve structured Pydantic error details in a secondary field while keeping the current string for compatibility.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_validates_extensions_config_semantics_without_creating_database -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py tests/test_extensions_config_sources.py tests/test_mcp_db_store.py tests/test_config_sources.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
git diff --check
```

Results:

- targeted extensions preflight test: `1 passed, 1 warning`
- related regression set: `34 passed, 1 warning`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

The migration dry-run now catches invalid extensions/MCP semantics before any DB mutation. This closes the parser-level-only validation gap for optional extensions config files.
