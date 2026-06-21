# Public Skill Deployment Artifact Boundary Review

Date: 2026-06-20

## Scope

- `scripts/import_runtime_state_to_db.py`
- `tests/test_import_runtime_state_to_db.py`
- `docs/harness-stateless-db-mode-operator-runbook.md`
- `docs/harness-stateless-db-mode-v1-scope-decisions.md`
- implementation log Batch 90

## Findings

### P2 Fixed: Public skill rows now expose their deployment artifact requirement

Before this batch, the runtime-state import report showed public skills as `import_action: file-backed-skip`, but it did not spell out the remaining runtime dependency. That was easy to miss during stateless rollout planning because custom skills and public skills appeared in the same `skills[]` inventory shape.

The report now adds stable boundary fields:

- custom skills: `storage_boundary: db`, `runtime_artifact_required: false`, `deployment_artifact: null`.
- public skills: `storage_boundary: deployment-artifact`, `runtime_artifact_required: true`, `deployment_artifact: gateway-public-skill-bundle`.

### P2 Fixed: Operator docs now name the checklist fields

The operator runbook and V1 scope decision now tell operators to inspect `skills[].storage_boundary`, `skills[].runtime_artifact_required`, and `skills[].deployment_artifact` during dry-run review.

### P2 Remaining: Public skill DB seeding is still outside V1

This batch does not change the product boundary. Public skills remain immutable gateway/runtime deployment artifacts in V1. A later public-skill DB seed path is still needed only if the rollout cannot guarantee a consistent gateway-side public skill bundle.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_apply_writes_custom_skills -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
uv --directory backend run pytest -q
uv --directory backend run ruff check .
git diff --check
```

Results:

- red check before implementation: `1 failed, 1 warning`
- targeted public-skill boundary test: `1 passed, 1 warning`
- import migration regression set: `23 passed, 1 warning`
- full backend suite: `4808 passed, 36 skipped, 12 warnings`
- ruff: `All checks passed`
- `git diff --check`: no output

## Conclusion

The V1 migration report now makes public-skill deployment ownership explicit enough for rollout checklists and automated report consumers, while preserving the documented V1 boundary that only custom skills are DB-backed mutable runtime state.
