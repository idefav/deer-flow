# Harness Stateless DB Mode Review: Strict Sandbox Runtime Context

Date: 2026-06-19

## Scope

- `SandboxConfig`
- `deerflow.sandbox.tools`
- sandbox materializer tests
- implementation log Batch 39

## Findings

### P2 Fixed: Strict stateless mode can fail closed

`SandboxConfig.runtime_context_fail_closed` now controls sandbox initialization behavior when DB-backed runtime context materialization fails. The default remains compatibility mode (`False`), but strict stateless deployments can set it to `True` to raise `SandboxRuntimeError`.

### P2 Fixed: Failure context is still preserved

Even in strict mode, the runtime context records `sandbox_context_error` before raising. This gives callers and logs a precise failure reason while preventing the sandbox from proceeding with missing or stale context files.

### P2 Fixed: Config-level payload coverage exists

In addition to the runtime behavior test, `AppConfig.from_payload()` now has coverage for `sandbox.runtime_context_fail_closed: true`. This proves DB/file payload loading can carry the strict flag into `SandboxConfig`.

### P2 Fixed In Batch 58: Operator guidance is documented

The code now supports both compatibility and strict modes. Batch 58 adds operator guidance to `docs/harness-stateless-db-mode-operator-runbook.md`:

- use compatibility mode for rollout and mixed file/DB deployments.
- use strict mode when prompt-visible sandbox context files are required for correctness.

## Verification

```bash
uv --directory backend run pytest tests/test_sandbox_materializer.py::test_ensure_sandbox_initialized_raises_when_strict_runtime_context_materialization_fails -q
uv --directory backend run pytest tests/test_sandbox_materializer.py -q
uv --directory backend run pytest tests/test_config_sources.py::test_app_config_from_payload_loads_strict_sandbox_runtime_context_flag -q
```

Results:

- `1 passed, 1 warning`
- `8 passed, 1 warning`
- `1 passed, 1 warning`

## Conclusion

Strict sandbox runtime-context materialization is now implemented as an explicit compatibility switch. This closes the fail-open risk for deployments that require fully stateless DB-backed context delivery before sandbox tools run.
