from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path

from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.skills_config import SkillsConfig
from deerflow.skills.storage import get_or_new_skill_storage, reset_skill_storage
from deerflow.skills.types import SKILL_MD_FILE, SkillCategory


def _db_config(tmp_path: Path) -> DatabaseConfig:
    return DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db"))


def _skill_content(name: str, description: str = "DB skill") -> str:
    return f"""---
name: {name}
description: {description}
---

Use the database.
"""


def _skill_content_with_metadata(name: str) -> str:
    return f"""---
name: {name}
description: Metadata from DB
license: MIT
allowed-tools:
  - shell
---

Use metadata persisted in the database.
"""


def test_db_skill_storage_persists_custom_skill_and_rematerializes(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))

    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))
    assert storage.read_custom_skill("db-skill") == _skill_content("db-skill")

    shutil.rmtree(cache_root / "custom")

    skills = storage.load_skills(enabled_only=False)
    loaded = next(skill for skill in skills if skill.name == "db-skill")
    assert loaded.category == SkillCategory.CUSTOM
    assert loaded.skill_file.read_text(encoding="utf-8") == _skill_content("db-skill")
    assert loaded.get_container_file_path() == "/mnt/skills/custom/db-skill/SKILL.md"


def test_db_skill_storage_support_files_history_and_delete(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))
    storage.write_custom_skill("db-skill", "references/notes.md", "supporting notes")
    storage.append_history("db-skill", {"action": "create", "prev_content": None, "new_content": "v1"})

    shutil.rmtree(cache_root / "custom")

    skill_dir = storage.get_custom_skill_dir("db-skill")
    assert (skill_dir / "references" / "notes.md").read_text(encoding="utf-8") == "supporting notes"
    assert storage.read_history("db-skill")[0]["action"] == "create"
    assert storage.get_skill_history_file("db-skill").exists()

    storage.delete_custom_skill("db-skill", history_meta={"action": "delete"})
    assert not storage.custom_skill_exists("db-skill")
    assert storage.read_history("db-skill")[-1]["action"] == "delete"
    assert not skill_dir.exists()


def test_db_skill_storage_deletes_support_file_from_database(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))
    storage.write_custom_skill("db-skill", "references/notes.md", "supporting notes")

    storage.delete_custom_skill_file("db-skill", "references/notes.md")
    assert not (cache_root / "custom" / "db-skill" / "references" / "notes.md").exists()

    shutil.rmtree(cache_root / "custom")
    skill_dir = storage.get_custom_skill_dir("db-skill")

    assert (skill_dir / SKILL_MD_FILE).exists()
    assert not (skill_dir / "references" / "notes.md").exists()


def test_db_skill_storage_prunes_stale_support_files_on_materialization(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))
    storage.write_custom_skill("db-skill", "references/keep.md", "keep this")

    stale_file = cache_root / "custom" / "db-skill" / "references" / "stale.md"
    stale_file.write_text("stale cache content", encoding="utf-8")

    skill_dir = storage.get_custom_skill_dir("db-skill")

    assert (skill_dir / "references" / "keep.md").read_text(encoding="utf-8") == "keep this"
    assert not stale_file.exists()


def test_db_skill_storage_prunes_deleted_custom_skill_dirs_on_load(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))

    ghost_dir = cache_root / "custom" / "ghost-skill"
    ghost_dir.mkdir(parents=True)
    (ghost_dir / SKILL_MD_FILE).write_text(_skill_content("ghost-skill"), encoding="utf-8")

    skills = storage.load_skills(enabled_only=False)

    assert [skill.name for skill in skills] == ["db-skill"]
    assert not ghost_dir.exists()


def test_db_skill_storage_load_skills_materializes_only_skill_md(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))
    storage.write_custom_skill("db-skill", "references/notes.md", "supporting notes")

    shutil.rmtree(cache_root / "custom")

    skills = storage.load_skills(enabled_only=False)

    assert [skill.name for skill in skills] == ["db-skill"]
    assert (cache_root / "custom" / "db-skill" / SKILL_MD_FILE).read_text(encoding="utf-8") == _skill_content("db-skill")
    assert not (cache_root / "custom" / "db-skill" / "references" / "notes.md").exists()


def test_db_skill_storage_load_skills_uses_db_metadata_without_reparsing_skill_md(tmp_path, monkeypatch) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content_with_metadata("db-skill"))

    stale_skill_file = cache_root / "custom" / "db-skill" / SKILL_MD_FILE
    stale_skill_file.write_text(_skill_content("db-skill", "stale cache metadata"), encoding="utf-8")

    def _fail_parse(*args, **kwargs):
        raise AssertionError("DB custom skill listing must not parse materialized SKILL.md")

    monkeypatch.setattr("deerflow.skills.parser.parse_skill_file", _fail_parse)

    skills = storage.load_skills(enabled_only=False)

    assert [skill.name for skill in skills] == ["db-skill"]
    loaded = skills[0]
    assert loaded.description == "Metadata from DB"
    assert loaded.license == "MIT"
    assert loaded.allowed_tools == ["shell"]
    assert stale_skill_file.read_text(encoding="utf-8") == _skill_content_with_metadata("db-skill")


def test_db_skill_storage_backfills_metadata_column_for_existing_table(tmp_path) -> None:
    from sqlalchemy import create_engine, inspect, text

    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    database = _db_config(tmp_path)
    Path(database.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE custom_skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id VARCHAR(128) NOT NULL DEFAULT '',
                    skill_name VARCHAR(128) NOT NULL,
                    skill_md_text TEXT NOT NULL DEFAULT '',
                    files_json JSON NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE (owner_user_id, skill_name)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_skills (
                    owner_user_id, skill_name, skill_md_text, files_json, revision, created_at, updated_at
                ) VALUES (
                    '', 'db-skill', :skill_md_text, '{}', 1, '2026-06-20T00:00:00', '2026-06-20T00:00:00'
                )
                """
            ),
            {"skill_md_text": _skill_content("db-skill", "legacy table metadata")},
        )

    storage = DbSkillStorage(host_path=str(tmp_path / "skills-cache"), database_config=database)
    skills = storage.load_skills(enabled_only=False)

    assert [skill.name for skill in skills] == ["db-skill"]
    assert skills[0].description == "legacy table metadata"
    assert "metadata_json" in {column["name"] for column in inspect(engine).get_columns("custom_skills")}


def test_db_skill_storage_reads_file_and_manifest_without_materializing_tree(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))
    storage.write_custom_skill("db-skill", "references/notes.md", "supporting notes")

    shutil.rmtree(cache_root / "custom")

    manifest = storage.list_skill_file_manifest("db-skill", "custom")

    assert [item.relative_path for item in manifest] == ["SKILL.md", "references/notes.md"]
    assert manifest[0].size == len(_skill_content("db-skill").encode("utf-8"))
    assert storage.read_skill_file("db-skill", "custom", "references/notes.md") == "supporting notes"
    assert not (cache_root / "custom" / "db-skill" / "references" / "notes.md").exists()


def test_db_skill_storage_write_collapses_duplicate_business_keys_without_db_unique_constraints(tmp_path) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from deerflow.persistence.skills.model import NormalizedSkillRow, SkillFileRow, SkillRow
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    database = _db_config(tmp_path)
    storage = DbSkillStorage(host_path=str(cache_root), database_config=database)
    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill"))

    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with Session(engine) as session:
        normalized_row = session.execute(
            select(NormalizedSkillRow).where(
                NormalizedSkillRow.category == "custom",
                NormalizedSkillRow.skill_name == "db-skill",
            )
        ).scalar_one()
        session.add_all(
            [
                SkillRow(
                    owner_user_id="",
                    skill_name="db-skill",
                    skill_md_text=_skill_content("db-skill", "duplicate legacy"),
                    metadata_json={},
                    files_json={},
                    revision=0,
                ),
                NormalizedSkillRow(
                    owner_user_id="",
                    category="custom",
                    skill_name="db-skill",
                    metadata_json={},
                    enabled=True,
                    revision=0,
                    source="duplicate",
                    content_hash="duplicate",
                ),
                SkillFileRow(
                    skill_id=normalized_row.id,
                    relative_path=SKILL_MD_FILE,
                    content_json="stale duplicate",
                    mode=None,
                    mime_type="text/plain; charset=utf-8",
                    size=15,
                    content_hash="duplicate",
                ),
            ]
        )
        session.commit()

    storage.write_custom_skill("db-skill", SKILL_MD_FILE, _skill_content("db-skill", "updated"))

    with Session(engine) as session:
        legacy_rows = list(session.execute(select(SkillRow).where(SkillRow.skill_name == "db-skill")).scalars())
        normalized_rows = list(
            session.execute(
                select(NormalizedSkillRow).where(
                    NormalizedSkillRow.category == "custom",
                    NormalizedSkillRow.skill_name == "db-skill",
                )
            ).scalars()
        )
        file_rows = list(session.execute(select(SkillFileRow).where(SkillFileRow.relative_path == SKILL_MD_FILE)).scalars())

    assert len(legacy_rows) == 1
    assert len(normalized_rows) == 1
    assert len(file_rows) == 1
    assert legacy_rows[0].skill_md_text == _skill_content("db-skill", "updated")
    assert storage.read_skill_file("db-skill", SkillCategory.CUSTOM, SKILL_MD_FILE) == _skill_content("db-skill", "updated")


def test_db_skill_storage_seeds_public_skill_from_db_without_filesystem(tmp_path) -> None:
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import Session

    from deerflow.persistence.skills.model import NormalizedSkillRow, SkillFileRow
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    seed_root = tmp_path / "seed-skills"
    public_skill = seed_root / "public" / "browser"
    public_skill.mkdir(parents=True)
    (public_skill / SKILL_MD_FILE).write_text(
        "---\nname: browser\ndescription: Browser skill\nallowed-tools:\n  - browser\n---\n",
        encoding="utf-8",
    )
    (public_skill / "references").mkdir()
    (public_skill / "references" / "usage.md").write_text("open pages on demand", encoding="utf-8")
    (public_skill / "assets").mkdir()
    (public_skill / "assets" / "logo.bin").write_bytes(b"\x89browser\xff")

    database = _db_config(tmp_path)
    storage = DbSkillStorage(host_path=str(cache_root), database_config=database)
    assert storage.seed_public_skills_from_root(seed_root) == 1

    shutil.rmtree(seed_root)
    shutil.rmtree(cache_root / "public", ignore_errors=True)

    skills = storage.load_skills(enabled_only=False)
    assert [skill.name for skill in skills] == ["browser"]
    loaded = skills[0]
    assert loaded.category == SkillCategory.PUBLIC
    assert loaded.description == "Browser skill"
    assert loaded.allowed_tools == ["browser"]
    assert loaded.get_container_file_path() == "/mnt/skills/public/browser/SKILL.md"
    assert (cache_root / "public" / "browser" / SKILL_MD_FILE).is_file()
    assert not (cache_root / "public" / "browser" / "references" / "usage.md").exists()

    manifest = storage.list_skill_file_manifest("browser", SkillCategory.PUBLIC)
    assert [item.relative_path for item in manifest] == [
        "SKILL.md",
        "assets/logo.bin",
        "references/usage.md",
    ]
    assert storage.read_skill_file("browser", SkillCategory.PUBLIC, "references/usage.md") == "open pages on demand"
    assert storage.read_skill_file("browser", SkillCategory.PUBLIC, "assets/logo.bin") == b"\x89browser\xff"

    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    assert {"skills", "skill_files"}.issubset(inspect(engine).get_table_names())
    with Session(engine) as session:
        skill_row = session.query(NormalizedSkillRow).filter_by(category="public", skill_name="browser").one()
        file_rows = session.query(SkillFileRow).filter_by(skill_id=skill_row.id).all()
    assert {row.relative_path for row in file_rows} == {"SKILL.md", "assets/logo.bin", "references/usage.md"}


def test_db_skill_storage_public_skill_missing_seed_fails_closed(tmp_path) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    public_skill = cache_root / "public" / "browser"
    public_skill.mkdir(parents=True)
    (public_skill / SKILL_MD_FILE).write_text(
        "---\nname: browser\ndescription: Browser skill\n---\n",
        encoding="utf-8",
    )

    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))

    assert storage.load_skills(enabled_only=False) == []
    assert storage.public_skill_exists("browser") is False
    try:
        storage.list_skill_file_manifest("browser", SkillCategory.PUBLIC)
    except FileNotFoundError as exc:
        assert "public" in str(exc).lower()
    else:
        raise AssertionError("DB mode public skill reads must fail closed without a DB seed")


def test_db_skill_storage_backfills_legacy_custom_rows_to_normalized_tables(tmp_path) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from deerflow.persistence.skills.model import NormalizedSkillRow, SkillFileRow
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    database = _db_config(tmp_path)
    Path(database.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database.sqlite_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE custom_skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id VARCHAR(128) NOT NULL DEFAULT '',
                    skill_name VARCHAR(128) NOT NULL,
                    skill_md_text TEXT NOT NULL DEFAULT '',
                    metadata_json JSON NOT NULL DEFAULT '{}',
                    files_json JSON NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE (owner_user_id, skill_name)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_skills (
                    owner_user_id, skill_name, skill_md_text, metadata_json, files_json, revision, created_at, updated_at
                ) VALUES (
                    '', 'legacy-skill', :skill_md_text, '{}',
                    '{"references/notes.md": "legacy support"}',
                    7, '2026-06-20T00:00:00', '2026-06-20T00:00:00'
                )
                """
            ),
            {"skill_md_text": _skill_content("legacy-skill", "Legacy custom skill")},
        )

    storage = DbSkillStorage(host_path=str(tmp_path / "skills-cache"), database_config=database)

    assert storage.read_custom_skill("legacy-skill").startswith("---\nname: legacy-skill")
    assert storage.read_skill_file("legacy-skill", "custom", "references/notes.md") == "legacy support"
    with Session(engine) as session:
        skill_row = session.query(NormalizedSkillRow).filter_by(category="custom", skill_name="legacy-skill").one()
        assert skill_row.revision == 7
        file_rows = session.query(SkillFileRow).filter_by(skill_id=skill_row.id).all()
    assert {row.relative_path for row in file_rows} == {"SKILL.md", "references/notes.md"}


def test_db_skill_storage_installs_binary_asset_from_archive(tmp_path, monkeypatch) -> None:
    from deerflow.skills.security_scanner import ScanResult
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    async def _scan(*args, **kwargs):
        return ScanResult(decision="allow", reason="ok")

    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)

    cache_root = tmp_path / "skills-cache"
    archive_path = tmp_path / "db-binary-skill.skill"
    binary_payload = b"\x89PNG\r\n\x1a\n\x00\xffbinary"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("db-binary-skill/SKILL.md", _skill_content("db-binary-skill"))
        zf.writestr("db-binary-skill/assets/logo.bin", binary_payload)

    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    result = storage.install_skill_from_archive(archive_path)
    assert result["skill_name"] == "db-binary-skill"

    shutil.rmtree(cache_root / "custom")
    skill_dir = storage.get_custom_skill_dir("db-binary-skill")

    assert (skill_dir / SKILL_MD_FILE).read_text(encoding="utf-8") == _skill_content("db-binary-skill")
    assert (skill_dir / "assets" / "logo.bin").read_bytes() == binary_payload


def test_db_skill_storage_preserves_script_executable_mode_from_archive(tmp_path, monkeypatch) -> None:
    from deerflow.skills.security_scanner import ScanResult
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    async def _scan(*args, **kwargs):
        return ScanResult(decision="allow", reason="ok")

    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)

    cache_root = tmp_path / "skills-cache"
    archive_path = tmp_path / "db-script-skill.skill"
    script_info = zipfile.ZipInfo("db-script-skill/scripts/run.sh")
    script_info.external_attr = (stat.S_IFREG | 0o755) << 16
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("db-script-skill/SKILL.md", _skill_content("db-script-skill"))
        zf.writestr(script_info, "#!/bin/sh\necho ok\n")

    storage = DbSkillStorage(host_path=str(cache_root), database_config=_db_config(tmp_path))
    storage.install_skill_from_archive(archive_path)

    shutil.rmtree(cache_root / "custom")
    script_path = storage.get_custom_skill_dir("db-script-skill") / "scripts" / "run.sh"

    assert script_path.read_text(encoding="utf-8") == "#!/bin/sh\necho ok\n"
    assert stat.S_IMODE(script_path.stat().st_mode) & 0o111 == 0o111


def test_db_mode_skill_storage_factory_defaults_to_db_storage(tmp_path, monkeypatch) -> None:
    from deerflow.skills.storage.db_skill_storage import DbSkillStorage

    cache_root = tmp_path / "skills-cache"
    database = _db_config(tmp_path)
    app_config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "database": database.model_dump(),
            "skills": SkillsConfig(path=str(cache_root)).model_dump(),
        }
    )
    set_app_config(app_config)
    monkeypatch.setenv("DEER_FLOW_CONFIG_SOURCE", "db")
    reset_skill_storage()
    try:
        storage = get_or_new_skill_storage(app_config=app_config)
    finally:
        reset_app_config()
        reset_skill_storage()

    assert isinstance(storage, DbSkillStorage)
