# Migration MCP Env Ref Risk Review

Date: 2026-06-20

## Scope

- `scripts/import_runtime_state_to_db.py`
- `tests/test_import_runtime_state_to_db.py`
- migration dry-run plan checklist
- operator runbook migration checklist
- implementation log Batch 98

## Findings

### P2 Fixed: Migration dry-run now reports MCP env references

The original migration plan required dry-run reporting for env refs, resolved-secret risks, stdio MCP risks, and invalid schemas. Before this batch, `extensions.risks` reported `resolved-secret` for literal secret-looking MCP fields and `stdio-mcp-stateful` for stdio servers, but `$ENV_NAME` values were treated as safe by omission rather than visible operator inputs.

Batch 98 reports `$ENV_NAME` values as non-blocking `env-ref` entries with the exact field and env variable name. This preserves the important distinction between deployment-time secret references and plaintext resolved secrets.

### P2 Fixed: Existing resolved-secret and stdio signals are preserved

The same helper now emits `env-ref` for values that start with `$` and keeps `resolved-secret` for non-empty literal values. The existing stdio MCP stateless warning and schema validation preflight behavior are unchanged.

### P2 Remaining: Env refs are visibility, not secret validation

Dry-run can show that a config depends on `MCP_AUTH_HEADER`, but it does not prove the variable is set in the eventual deployment environment or that the value is valid for the remote MCP server. That check remains part of deployment/runtime validation.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_sources_and_risks -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `1 failed, 1 warning`
- targeted inventory check: `1 passed, 1 warning`
- migration import suite: `23 passed, 1 warning`
- full backend regression: `4825 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: clean

## Conclusion

The migration dry-run risk report now covers all four locally verifiable categories from the original migration plan: env references, resolved plaintext secret risks, stdio MCP stateless risks, and schema validation errors.
