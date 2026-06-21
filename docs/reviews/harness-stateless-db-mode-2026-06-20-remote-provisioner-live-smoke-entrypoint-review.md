# Remote Provisioner Live Smoke Entrypoint Review

Date: 2026-06-20

Reviewed artifacts:

- `backend/tests/test_aio_sandbox_remote_live.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-requirement-audit.md`
- `docs/harness-stateless-db-mode-implementation-log.md`

## Findings

### P1 Fixed: Remote provisioner/K8s smoke now has an executable test entrypoint

Before Batch 74, the requirement audit correctly kept remote provisioner/K8s validation as a production gate, but there was no repo-owned command that operators could run. The new opt-in test exercises `RemoteSandboxBackend`, provisioner `extra_mounts`, AIO file-API materialization, prompt-exposed skills-path reads, binary file reads, and sandbox cleanup.

### P1 Still Open: The smoke must be executed in a real deployment

The current workstation has no remote provisioner/K8s environment configured, so the new test is verified only for clean default skip behavior. Production rollout still requires running it with real values for `DEER_FLOW_REMOTE_AIO_PROVISIONER_URL` and a node-valid skills host path or prefix.

### P2 Risk: Host-path assumptions remain deployment-specific

The bundled provisioner maps `extra_mounts.host_path` to K8s `hostPath`. That is useful for the current provisioner implementation, but some clusters should translate the same contract into a PVC, `emptyDir`, or another managed volume. The smoke is written around the public `extra_mounts` contract, while the runbook keeps the host-path requirement explicit for the bundled provisioner.

## Verification

Command run:

```bash
uv --directory backend run pytest tests/test_aio_sandbox_remote_live.py -q
```

Result:

```text
1 skipped, 1 warning in 0.16s
```

## Conclusion

The remote live-smoke gap is now operationally actionable: the repo contains the exact opt-in smoke command and environment contract. The remaining gate is execution against the target K8s/provisioner deployment.
