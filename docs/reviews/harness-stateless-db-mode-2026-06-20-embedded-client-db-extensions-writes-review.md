# Embedded Client DB Extensions Writes Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/client.py`
- `backend/tests/test_client.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: Embedded MCP config writes now honor DB mode

`DeerFlowClient.update_mcp_config()` previously resolved and wrote `extensions_config.json` unconditionally. In DB mode it now writes through `DbExtensionsConfigStore`, preserves existing skill state and extension extras, uses optimistic revision checks, reloads the active extensions config, resets the MCP tools cache, and invalidates the embedded agent.

### P1 Fixed: Embedded skill toggles now honor DB mode

`DeerFlowClient.update_skill()` previously toggled skill state only by writing `extensions_config.json`. In DB mode it now updates `runtime_configs.extensions`, preserves existing MCP servers and extension extras, uses the same revision-conflict guard as Gateway DB writes, reloads extensions config, and invalidates the embedded agent before returning the refreshed skill state.

### P2 Remaining: External live gates are still required

This batch closes a local embedded-client write-surface gap. It does not execute the remote provisioner/K8s smoke or real model live gates, which still need a deployed cluster and model credentials.

## Verification

Red checks:

```bash
uv --directory backend run pytest tests/test_client.py::TestMcpConfig::test_update_mcp_config_db_mode_writes_runtime_config tests/test_client.py::TestSkillsManagement::test_update_skill_db_mode_writes_runtime_config -q
```

Result:

```text
2 failed, 1 warning in 0.68s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_client.py::TestMcpConfig tests/test_client.py::TestSkillsManagement -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
11 passed, 1 warning in 0.56s
4800 passed, 36 skipped, 12 warnings in 84.59s
All checks passed!
git diff --check produced no output.
```

## Conclusion

The embedded Python client now follows the same DB-backed extensions write boundary as Gateway for MCP config and skill enabled-state changes. Remaining completion risks are still external environment validation gates.
