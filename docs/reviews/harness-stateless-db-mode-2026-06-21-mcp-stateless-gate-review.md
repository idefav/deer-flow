# Review: MCP Strict Stateless Gate

Date: 2026-06-21

## Scope

Reviewed the close-out change that adds an MCP strict-stateless compatibility gate and exposes the same compatibility shape in migration dry-run.

## Findings

- No blocking issues found in the implemented scope.
- HTTP and SSE MCP transports are treated as strict-stateless compatible remote services.
- Enabled stdio MCP servers are blocked by `mcp_stateless` unless they explicitly declare `stateless.runtime_mode` as `sticky`, `sidecar`, or `single-node`.
- DB mode reads the first-class `mcp_servers` table, so the gate checks the same DB source as runtime startup.
- Migration dry-run now emits `extensions.mcp_compatibility`, aligning operator preflight output with live-gate evidence.

## Residual Risk

- The DB stores MCP configuration, not stdio MCP process/session state.
- Declaring `sticky`, `sidecar`, or `single-node` is an operational compatibility assertion; the gate cannot prove sidecar wiring without a deployment-specific smoke.

## Verification Reviewed

```text
Red: 3 failed, 1 warning in 0.37s
Focused MCP gate tests: 3 passed, 1 warning in 0.22s
Focused migration compatibility tests: 3 passed, 1 warning in 0.39s
```
