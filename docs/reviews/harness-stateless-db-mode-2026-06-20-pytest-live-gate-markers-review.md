# Pytest Live Gate Markers Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/pyproject.toml`
- `backend/tests/test_client_live.py`
- `backend/tests/test_aio_sandbox_live.py`
- `backend/tests/test_aio_sandbox_remote_live.py`
- `backend/tests/test_create_deerflow_agent_live.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

## Findings

### P1 Fixed: External gates are now discoverable by pytest marker

Before Batch 75, live gates were discoverable only by file names and environment-variable conventions. `pytest -m live` collected no tests. Batch 75 registers `live`, `remote_live`, and `requires_llm` markers and applies them to the current live gate modules.

### P1 Fixed: Module-level skip behavior is preserved for live client tests

`tests/test_client_live.py` already had module-level skip behavior for CI, missing `config.yaml`, or missing configured models. Batch 75 changes `pytestmark` to a list, so the new `live` / `requires_llm` markers coexist with the existing skip marker instead of replacing it.

### P2 Remaining: Markers do not replace real external execution

The new markers make live gates easier to collect and run. They do not prove remote provisioner/K8s permissions or real model behavior in this workstation, where those tests skip by design.

## Verification

Red collection check before markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py -q
```

Result:

```text
no tests collected (21 deselected) in 0.28s
exit code 5
```

Green collection check after markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
```

Result:

```text
24 tests collected in 0.34s
```

Default no-credentials/no-cluster execution:

```bash
uv --directory backend run pytest -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
```

Result:

```text
24 skipped, 1 warning in 0.34s
```

Remote-only gate selection:

```bash
uv --directory backend run pytest --collect-only -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
uv --directory backend run pytest -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py -q
```

Result:

```text
1/24 tests collected (23 deselected) in 0.32s
1 skipped, 23 deselected, 1 warning in 0.32s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4792 passed, 36 skipped, 12 warnings in 83.99s
All checks passed!
git diff --check produced no output.
```

## Conclusion

The remaining live validation gates are now first-class pytest selections. Operators can run all live gates with `pytest -m live` or cluster-backed gates with `pytest -m remote_live`, while unconfigured local and CI environments continue to skip cleanly.
