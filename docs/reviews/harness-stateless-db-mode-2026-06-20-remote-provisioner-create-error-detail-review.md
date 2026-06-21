# Remote Provisioner Create Error Detail Review

Date: 2026-06-20

## Scope

- `packages/harness/deerflow/community/aio_sandbox/remote_backend.py`
- `tests/test_remote_sandbox_backend.py`
- remote provisioner/K8s live smoke diagnostics
- implementation log Batch 92

## Findings

### P2 Fixed: Remote create failures now preserve provisioner response bodies

`RemoteSandboxBackend._provisioner_create()` previously wrapped `requests.RequestException` as `RuntimeError("Provisioner create failed: ...")`, but only included the exception summary. For HTTP status failures, that usually meant operators saw `HTTP 409` without the provisioner response body.

That was not enough after Batch 91, where the bundled provisioner can reject stale existing Pods with a mount-contract mismatch and an actionable remediation message. Batch 92 appends `exc.response.text` to the RuntimeError/log detail when available.

### P2 Fixed: Remote live smoke diagnostics now carry mount-contract remediation

The new regression test simulates the provisioner returning a 409 response body containing the mount-contract mismatch guidance. The raised RuntimeError now includes both the high-level create failure and the response body text, so remote live smoke output can tell operators what to destroy or reconfigure.

### P2 Remaining: Remote live smoke still requires a real deployment

This batch improves diagnostics for failed remote create attempts. It does not prove K8s node path/PVC permissions or the AIO file API user's write access. That still requires running the opt-in remote live smoke in the target environment.

## Verification

```bash
uv --directory backend run pytest tests/test_remote_sandbox_backend.py::test_provisioner_create_includes_response_body_in_runtime_error -q
uv --directory backend run pytest tests/test_remote_sandbox_backend.py -q
uv --directory backend run ruff check packages/harness/deerflow/community/aio_sandbox/remote_backend.py tests/test_remote_sandbox_backend.py
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `1 failed, 1 warning`
- targeted response-body propagation test: `1 passed, 1 warning`
- remote backend regression set: `23 passed, 1 warning`
- remote live smoke default workstation behavior: `1 skipped, 1 warning`
- full backend suite: `4812 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

Remote provisioner create failures now preserve the provisioner response body, making Batch 91 mount-contract rejections visible at the gateway/test layer instead of collapsing them into a generic HTTP status.
