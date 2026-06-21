# Migration Channel Runtime Inventory Review

Date: 2026-06-20

## Scope

- `scripts/import_runtime_state_to_db.py`
- `tests/test_import_runtime_state_to_db.py`
- channel runtime dry-run inventory
- operator runbook migration checklist
- requirement audit Batch 100

## Findings

### P2 Fixed: Migration dry-run now discovers channel runtime config

`ChannelRuntimeConfigStore` persists UI-entered IM channel runtime state at `.deer-flow/channels/runtime-config.json`. Before this batch, the import inventory reported app config, extensions/MCP, agents, memory, and skills, but omitted that runtime channel file even though the technical design requires channel runtime config discovery.

Batch 100 adds `channel_runtime` to the inventory report with the source path, provider summaries, and `summary.channel_runtime_configs`.

### P2 Fixed: Channel runtime credential risks are visible before apply

Runtime channel files can contain provider credentials such as Slack bot tokens. Batch 100 reports secret-like provider fields through `channel_runtime.risks` using the same `env-ref` and `resolved-secret` semantics as app config and MCP risk reporting.

### P2 Fixed: Invalid channel runtime JSON blocks preflight

Malformed channel runtime JSON now appears in `preflight.errors` with `resource: channel_runtime_config` and `code: invalid_channel_runtime_config`. Dry-run remains non-mutating and does not create the target DB when this preflight fails.

### P2 Remaining: Channel runtime apply remains a product boundary

This batch makes channel runtime state visible and validated in dry-run. It does not yet migrate that state into a DB-backed channel runtime store. If V1 requires stateless channel runtime configuration beyond detection/reporting, the apply path and DB store contract need a dedicated follow-up.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_channel_runtime_config tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_reports_invalid_channel_runtime_config -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `2 failed, 1 warning`
- targeted channel runtime checks: `2 passed, 1 warning`
- migration import suite: `26 passed, 1 warning`
- full backend regression: `4828 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: clean

## Conclusion

The migration dry-run now covers the channel runtime file called out in the technical design and makes its credential and schema risks visible before any DB apply.
