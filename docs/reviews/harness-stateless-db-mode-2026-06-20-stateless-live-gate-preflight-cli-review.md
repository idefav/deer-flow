# Stateless Live Gate Preflight CLI Review

Date: 2026-06-20

## Scope

- `scripts/check_stateless_live_gates.py`
- `tests/test_stateless_live_gate_check.py`
- `tests/test_live_gate_markers.py`
- operator runbook live-gate instructions
- implementation log Batch 94

## Findings

### P2 Fixed: Remaining live gates now have a machine-checkable preflight

Before this batch, the runbook listed the remote, Docker, and model-backed live commands, but operators had to manually infer whether the current shell had enough environment to invoke them. Batch 94 adds `scripts/check_stateless_live_gates.py`, which reports selected gates, required environment, missing/invalid values, manual prerequisites, and the exact pytest command.

The script exits with code `2` when selected gates are not ready to invoke, so rollout automation can fail before a remote live smoke silently skips because required environment variables are absent.

### P2 Fixed: Remote live host path requirement is explicit

The remote live smoke accepts either `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH` or `DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX`. The new preflight checks that one of those values exists, preventing an operator from setting only the provisioner URL and opt-in flag while still missing the writable node-path contract.

### P2 Remaining: External runtime proof still requires real environments

The preflight cannot prove that the provisioner is reachable, the Kubernetes node path is writable by the sandbox user, Docker can start the configured image, or model credentials are valid. It lists these as manual prerequisites and leaves the actual live gate execution as the rollout proof.

### P3 Fixed: Live marker detector no longer treats unit-test strings as live opt-ins

The marker regression guard originally searched for opt-in environment variable names as raw substrings. The new preflight tests intentionally assert those names in ordinary unit-test output, which triggered a false marker requirement. Batch 94 updates the detector to use AST checks for real `os.getenv(...)`, `os.environ.get(...)`, or `os.environ[...]` reads before requiring live markers.

## Verification

```bash
uv --directory backend run pytest tests/test_stateless_live_gate_check.py -q
uv --directory backend run pytest tests/test_live_gate_markers.py -q
uv --directory backend run ruff check scripts/check_stateless_live_gates.py tests/test_stateless_live_gate_check.py
uv --directory backend run ruff check tests/test_live_gate_markers.py
uv --directory backend run python scripts/check_stateless_live_gates.py --gate remote_live --json
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `1 error`
- focused preflight test suite: `4 passed, 1 warning`
- live marker guard suite: `3 passed, 1 warning`
- focused aggregate with remote live entrypoint: `7 passed, 1 skipped, 1 warning`
- full backend regression: `4817 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- default workstation remote preflight: exit code `2` with the expected missing remote-live environment variables
- `git diff --check`: clean

## Conclusion

The remaining external gates are now easier to run consistently and to wire into rollout automation, while the requirement audit correctly keeps real remote and model-backed live execution open.
