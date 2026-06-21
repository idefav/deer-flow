# Review: Requires LLM Project Root Preflight Parity

Date: 2026-06-20

## Scope

Reviewed Batch 109 changes that align `scripts/check_stateless_live_gates.py --gate requires_llm` file-mode config discovery with runtime `AppConfig.resolve_config_path()` behavior for `DEER_FLOW_PROJECT_ROOT`.

## Findings

- No blocking issues found in the implemented scope.
- `DEER_FLOW_CONFIG_PATH` remains the highest-priority file-mode source, preserving the explicit override contract.
- `DEER_FLOW_PROJECT_ROOT` is only consulted when no explicit config path is set, matching the runtime path where `existing_project_file(("config.yaml",))` searches the caller project root.
- Invalid project-root values now fail as `config_issues` before pytest invocation, which is appropriate for a preflight gate.
- The tests cover valid project-root config, missing project-root path, and project-root set to a file.

## Residual Risk

- This batch does not broaden preflight to the runtime legacy monorepo fallback candidates. That is acceptable for the live-gate operator contract because the stateless path should use explicit file/DB config source evidence, but it remains a known difference from the final legacy fallback in `AppConfig.resolve_config_path()`.
- Real provider credential validity is still outside preflight scope and must be proven by running `--gate requires_llm --run` in a live model environment.

## Verification Reviewed

```text
Red: 1 failed, 1 warning in 0.20s
Focused: 21 passed, 1 warning in 0.19s
Focused ruff: All checks passed!
Full pytest: 4850 passed, 36 skipped, 12 warnings in 85.78s
Full ruff: All checks passed!
git diff --check: no output
```
