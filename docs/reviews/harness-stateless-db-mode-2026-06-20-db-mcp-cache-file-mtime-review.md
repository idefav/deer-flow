# DB MCP Cache File Mtime Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/packages/harness/deerflow/mcp/cache.py`
- `backend/tests/test_mcp_cache_revision.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P1 Fixed: DB-mode MCP cache no longer resolves local extensions config paths

`_is_cache_stale()` already compared the DB-backed extensions revision, but when the revision was unchanged it still called `_get_config_mtime()`. That path resolved `DEER_FLOW_EXTENSIONS_CONFIG_PATH`; if an operator left a stale or missing file path in the environment, DB/stateless mode could fail while checking MCP cache freshness. Batch 84 makes `_get_config_mtime()` return `None` immediately in DB config mode.

### P1 Guarded: Missing file env vars cannot break DB-mode stale checks

`test_db_mcp_cache_stale_check_ignores_missing_file_config_path` sets DB mode plus a missing `DEER_FLOW_EXTENSIONS_CONFIG_PATH`, primes the MCP cache with the current DB revision, and asserts the stale check returns `False` instead of raising `FileNotFoundError`.

### P1 Preserved: DB revision reload behavior still works

The existing MCP cache revision tests still pass. DB mode continues to detect revision changes and reload cached MCP tools when the extensions revision changes.

### P2 Remaining: External live gates are still required

This batch closes a local DB-mode file-path dependency in MCP cache freshness checks. It does not execute the remote provisioner/K8s smoke or real model live gates.

## Verification

Red check:

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py::test_db_mcp_cache_stale_check_ignores_missing_file_config_path -q
```

Result:

```text
1 failed, 1 warning in 0.42s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_mcp_cache_revision.py -q
uv --directory backend run ruff check packages/harness/deerflow/mcp/cache.py tests/test_mcp_cache_revision.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
3 passed, 1 warning in 0.44s
All checks passed!
4803 passed, 36 skipped, 12 warnings in 84.02s
All checks passed!
git diff --check produced no output.
```

## Conclusion

MCP cache freshness in DB/stateless mode now depends on DB revisions only and no longer has a hidden dependency on local extensions config file paths.
