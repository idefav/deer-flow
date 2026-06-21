# Live Client Skip Gate Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/tests/test_client_live.py`
- full backend pytest output
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: Live client tests failed in no-model environments instead of skipping

`tests/test_client_live.py` already documented that it requires a working config and valid API credentials. It skipped in CI and when `config.yaml` was missing, but not when `config.yaml` existed with an empty `models` list. In this workstation, that caused full backend pytest to fail before any live model call:

```text
IndexError: list index out of range
No models are configured in /Users/idefav/Documents/sources/deer-flow/config.yaml.
```

The module now loads app config during collection and marks live tests skipped if no models are configured or config loading fails. This keeps the live suite active in properly configured environments while letting ordinary backend verification run cleanly in no-model environments.

### P1 Fixed: Explicit live-client run now exits cleanly when skipped

The first implementation used module-level `pytest.skip()`, which reported a skip but returned pytest exit code 5 when `tests/test_client_live.py` was run alone. Switching to `pytestmark = pytest.mark.skip(...)` collects the tests and skips them normally:

```text
19 skipped, 1 warning in 0.30s
```

### P2 Remaining: Live model behavior still needs a configured environment

This change does not claim live model behavior was exercised. It makes the environment gate explicit. A real model-configured run should still execute `tests/test_client_live.py` without the skip mark.

## Verification

Commands run:

```bash
uv --directory backend run pytest tests/test_client_live.py -q
uv --directory backend run pytest -q
```

Results:

```text
19 skipped, 1 warning in 0.30s
4787 passed, 35 skipped, 12 warnings in 84.44s
```

## Conclusion

Full backend verification is now green in the current no-model environment without deleting the live-client gate. The remaining live-client proof is intentionally environment-dependent.
