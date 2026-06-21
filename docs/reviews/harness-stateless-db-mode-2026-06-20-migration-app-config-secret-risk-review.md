# Migration App Config Secret Risk Review

Date: 2026-06-20

## Scope

- `scripts/import_runtime_state_to_db.py`
- `tests/test_import_runtime_state_to_db.py`
- app config migration inventory reporting
- operator runbook migration checklist
- technical design dry-run reporting notes
- implementation log Batch 99

## Findings

### P2 Fixed: Migration dry-run now reports app config secret/env-reference risks

The migration plan requires dry-run visibility for env refs and resolved-secret risks before DB apply. Before this batch, `_config_report()` only reported `config.yaml` presence, top-level keys, and YAML errors. Sensitive app config fields such as `models[].api_key` were invisible in the risk report.

Batch 99 adds `config.risks` and includes it in `summary.risks`. Values that start with `$` are reported as `env-ref`, while non-empty literal values are reported as `resolved-secret`.

### P2 Fixed: Risk scanning avoids broad literal-value noise

The scanner only reports values whose field names indicate secret material, including API keys, authorization fields, tokens, secrets, and passwords. Ordinary model names, provider class paths, URLs, and non-secret config values are not reported as risks solely because they are literal strings.

### P2 Remaining: Dry-run does not validate deployment secret availability

`env-ref` entries prove the source config depends on an environment variable. They do not prove that the variable exists in the target runtime or that the credential works with the provider. Live-gate preflight and real provider execution remain separate rollout checks.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_collect_runtime_state_inventory_reports_app_config_secret_risks -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `1 failed, 1 warning`
- targeted app config risk check: `1 passed, 1 warning`
- migration import suite: `24 passed, 1 warning`
- full backend regression: `4826 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: clean

## Conclusion

The migration dry-run report now covers both app config and MCP secret/env-reference risks before DB apply, making the original dry-run visibility requirement materially stronger.
