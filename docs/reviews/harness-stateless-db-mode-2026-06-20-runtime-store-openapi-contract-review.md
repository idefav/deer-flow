# Runtime Store OpenAPI Contract Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/app/gateway/routers/mcp.py`
- `backend/app/gateway/routers/skills.py`
- `backend/tests/test_runtime_store_openapi_contract.py`
- `docs/harness-stateless-db-mode-implementation-log.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`

## Findings

### P2 Fixed: Runtime mutation APIs no longer describe DB-capable writes as file-only

The MCP and skill update routes already had DB-mode branches, but their OpenAPI descriptions still said the operation saved to a file or modified `extensions_config.json`. Batch 83 updates those route contracts to describe the active extensions configuration source, matching the DB/file facade used by the implementation.

### P2 Guarded: Future route descriptions are covered by an OpenAPI contract test

`tests/test_runtime_store_openapi_contract.py` builds a small FastAPI app with the MCP and skills routers and inspects generated OpenAPI descriptions. The test prevents these DB-mode mutation endpoints from regressing to file-only wording.

### P2 Confirmed: No behavior change to DB or file persistence paths

The patch changes descriptions/docstrings only. DB mode still persists through `DbExtensionsConfigStore`; file mode still writes the file-backed extensions source. Existing MCP/skill router and client behavior is covered by the broader suite.

### P2 Remaining: External live gates are still required

This batch closes a local API contract/documentation mismatch. It does not execute the remote provisioner/K8s smoke or real model live gates.

## Verification

Red check:

```bash
uv --directory backend run pytest tests/test_runtime_store_openapi_contract.py -q
```

Result:

```text
1 failed, 1 warning in 0.58s
```

Green checks:

```bash
uv --directory backend run pytest tests/test_runtime_store_openapi_contract.py -q
uv --directory backend run ruff check app/gateway/routers/mcp.py app/gateway/routers/skills.py tests/test_runtime_store_openapi_contract.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Result:

```text
1 passed, 1 warning in 0.50s
All checks passed!
4802 passed, 36 skipped, 12 warnings in 84.13s
All checks passed!
git diff --check produced no output.
```

## Conclusion

Gateway API contracts now align with the stateless DB-mode design: mutable MCP and skill state is exposed as active runtime configuration state, not as file-only writes.
