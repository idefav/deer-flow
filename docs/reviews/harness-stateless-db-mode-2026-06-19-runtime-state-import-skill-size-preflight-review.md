# Runtime State Import Skill Size Preflight Review

Date: 2026-06-19

## Scope

- `scripts/import_runtime_state_to_db.py`
- custom skill support-file inventory
- migration preflight blocking behavior
- `tests/test_import_runtime_state_to_db.py`
- implementation log Batch 51

## Findings

### P1 Fixed: Oversized custom skill support files are reported before DB import

The migration inventory now calculates total support-file bytes per skill and emits a `skill-file-too-large` risk for each support file larger than the configured V1 DB import threshold.

The threshold defaults to 5 MiB and can be overridden with:

```bash
DEER_FLOW_DB_SKILL_FILE_MAX_BYTES
```

Invalid env values fall back to the default rather than breaking dry-run inventory.

### P1 Fixed: Oversized files block apply through preflight

The same risk is converted into a blocking `skill_file` preflight error. This prevents `--apply` from silently writing large assets into `custom_skills.files_json`.

This matches the documented V1 policy:

- small support files may be stored in DB.
- large assets must be reported before apply.
- V1 should not imply unlimited DB blob storage.

### P2: The limit is per file, not per skill package

The current guard catches single large support files. It does not yet enforce an aggregate package size limit across many small files.

Potential follow-up:

- add `DEER_FLOW_DB_SKILL_PACKAGE_MAX_BYTES` if operators see DB growth from many small support files.

### P2: Long-term storage shape is still unchanged

This batch does not change `DbSkillStorage` storage shape. Support files remain in JSON payloads for V1 compatibility.

Long-term options remain:

- move support files to a dedicated `custom_skill_files` table.
- use object storage for large binary assets.
- keep only metadata/manifest rows in DB and materialize assets separately.

## Verification

```bash
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py::test_import_runtime_state_preflight_blocks_oversized_skill_support_files -q
uv --directory backend run pytest tests/test_import_runtime_state_to_db.py -q
uv --directory backend run ruff check scripts/import_runtime_state_to_db.py tests/test_import_runtime_state_to_db.py
```

Results:

- targeted oversized support-file test: `1 passed, 1 warning`
- import migration regression set: `20 passed, 1 warning`
- ruff: `All checks passed`

## Conclusion

The migration path now has a concrete V1 guard against oversized custom skill support files. Operators get the warning in dry-run, and apply is blocked before large assets are written into DB-backed skill storage.
