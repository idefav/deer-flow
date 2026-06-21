# Real-LLM Live Gate Marker Coverage Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/tests/test_client_e2e.py`
- `backend/tests/test_deferred_tool_promotion_real_llm.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

## Findings

### P1 Fixed: Real-LLM E2E gates are no longer invisible to `pytest -m live`

Batch 75 added pytest markers for the obvious live files, but `test_client_e2e.py` and `test_deferred_tool_promotion_real_llm.py` still contained real-LLM gates that were selected only by file name or bespoke environment flags. Batch 76 adds marker coverage so operator live-gate selection includes them.

### P1 Fixed: `test_client_e2e.py` marker granularity is precise

`test_client_e2e.py` also contains non-LLM file/config/memory tests that should keep running in the normal suite. The existing `@requires_llm` decorator now applies both `live` and `requires_llm` markers only to the real model tests, while preserving the original skip behavior when credentials are absent.

### P2 Remaining: Marked gates still require real external execution

This change improves gate discovery and default local behavior. It does not prove model quality, one-api behavior, or provisioner/K8s writability in this workstation because those tests skip without credentials and cluster configuration.

## Verification

Red collection check before markers:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
no tests collected (44 deselected) in 0.25s
exit code 5
```

Focused green checks:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest -m live tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
12/44 tests collected (32 deselected) in 0.30s
12/44 tests collected (32 deselected) in 0.30s
12 skipped, 32 deselected, 1 warning in 0.29s
```

Full marked-gate collection:

```bash
uv --directory backend run pytest --collect-only -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest --collect-only -m requires_llm tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest --collect-only -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
36/68 tests collected (32 deselected) in 0.36s
34/68 tests collected (34 deselected) in 0.36s
1/68 tests collected (67 deselected) in 0.36s
```

Default no-credentials/no-cluster execution:

```bash
uv --directory backend run pytest -m live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest -m requires_llm tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
uv --directory backend run pytest -m remote_live tests/test_client_live.py tests/test_aio_sandbox_live.py tests/test_aio_sandbox_remote_live.py tests/test_create_deerflow_agent_live.py tests/test_client_e2e.py tests/test_deferred_tool_promotion_real_llm.py -q
```

Result:

```text
36 skipped, 32 deselected, 1 warning in 0.38s
34 skipped, 34 deselected, 1 warning in 0.38s
1 skipped, 67 deselected, 1 warning in 0.37s
```

Full regression:

```bash
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
4792 passed, 36 skipped, 12 warnings in 83.91s
All checks passed!
git diff --check produced no output.
```

## Conclusion

The marked live-gate surface now covers the known real model, local AIO, remote AIO, and one-api E2E gates. The remaining completion gates are actual execution in configured external environments, not missing local test selection.
