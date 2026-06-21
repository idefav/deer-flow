# Full Verification Compatibility Review

Date: 2026-06-19

Reviewed change set:

- full backend verification results
- `reload_extensions_config(None)` compatibility fix

## Findings

### P1 Fixed: ACP tests are compatible with monkeypatched `ExtensionsConfig.from_file`

Full backend verification exposed a compatibility issue where `reload_extensions_config(None)` passed `None` as a positional argument. Some tests monkeypatch `ExtensionsConfig.from_file` as a zero-argument classmethod after `cls`, so the extra `None` broke ACP tool tests.

The fix calls `ExtensionsConfig.from_file()` with no argument when `config_path is None`, while preserving explicit path behavior.

### P1: Live client tests still require local model configuration

The only remaining full-suite failures are in `tests/test_client_live.py`. They fail because the current local `config.yaml` has no models configured, causing `create_chat_model()` to index an empty model list before any live call can run.

Required follow-up:

- Provide a valid local model config before running live tests.
- Or keep CI/non-live verification excluding `tests/test_client_live.py` unless live credentials/config are provisioned.

## Positive Checks

- ACP focused suite is green after the compatibility fix.
- Non-live backend suite is green.
- Full ruff and whitespace checks are green.

## Verification Evidence

```bash
uv --directory backend run pytest tests/test_invoke_acp_agent_tool.py -q
```

Result:

```text
17 passed, 1 warning in 0.40s
```

```bash
uv --directory backend run pytest -q --ignore=tests/test_client_live.py
```

Result:

```text
4738 passed, 15 skipped, 12 warnings in 81.33s
```

```bash
uv --directory backend run ruff check
git diff --check
```

Result:

```text
All checks passed.
git diff --check produced no output.
```

## Review Conclusion

The implementation is clean against the non-live backend suite and lint checks. Full live verification is blocked by environment configuration, not by the DB/stateless changes.
