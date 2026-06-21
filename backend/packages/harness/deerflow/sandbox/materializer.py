"""Materialize DB-backed runtime context files into a sandbox."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from deerflow.sandbox.sandbox import Sandbox

DEFAULT_CONTEXT_ROOT = "/tmp/deerflow/context"
MANIFEST_FILENAME = ".manifest.json"


@dataclass(frozen=True)
class SandboxMaterializedFile:
    path: str
    content: str | bytes


@dataclass(frozen=True)
class SandboxMaterializerManifest:
    files: list[SandboxMaterializedFile]
    revision: str


@dataclass(frozen=True)
class SandboxMaterializerResult:
    changed: bool
    manifest_hash: str
    files_written: int


class SandboxMaterializer:
    """Write a deterministic runtime context manifest into any sandbox."""

    def __init__(self, *, context_root: str = DEFAULT_CONTEXT_ROOT, skills_root: str | None = None) -> None:
        self._context_root = context_root.rstrip("/") or "/"
        self._skills_root = skills_root.rstrip("/") if skills_root else None

    @property
    def manifest_path(self) -> str:
        return f"{self._context_root}/{MANIFEST_FILENAME}"

    @staticmethod
    def _normalize_relative_path(path: str) -> str:
        pure_path = PurePosixPath(path.replace("\\", "/"))
        if pure_path.is_absolute() or ".." in pure_path.parts or str(pure_path) in {"", "."}:
            raise ValueError(f"Materialized sandbox path must be a safe relative path: {path!r}")
        return pure_path.as_posix()

    @staticmethod
    def _content_bytes(content: str | bytes) -> bytes:
        return content.encode("utf-8") if isinstance(content, str) else content

    @classmethod
    def _file_record(cls, file: SandboxMaterializedFile) -> dict[str, Any]:
        data = cls._content_bytes(file.content)
        return {
            "path": cls._normalize_relative_path(file.path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "binary": isinstance(file.content, bytes),
        }

    @classmethod
    def _manifest_payload(cls, manifest: SandboxMaterializerManifest) -> dict[str, Any]:
        files = sorted((cls._file_record(file) for file in manifest.files), key=lambda item: item["path"])
        payload = {
            "revision": manifest.revision,
            "files": files,
        }
        payload["manifest_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return payload

    @staticmethod
    def _read_existing_manifest_payload(sandbox: Sandbox, manifest_path: str) -> dict[str, Any] | None:
        try:
            raw = sandbox.read_file(manifest_path)
        except (FileNotFoundError, OSError):
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _read_existing_manifest_hash(cls, sandbox: Sandbox, manifest_path: str) -> str | None:
        payload = cls._read_existing_manifest_payload(sandbox, manifest_path)
        if payload is None:
            return None
        manifest_hash = payload.get("manifest_hash")
        return manifest_hash if isinstance(manifest_hash, str) else None

    @classmethod
    def _manifest_file_paths(cls, payload: dict[str, Any] | None) -> set[str]:
        if payload is None:
            return set()
        files = payload.get("files")
        if not isinstance(files, list):
            return set()
        paths: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not isinstance(path, str):
                continue
            try:
                paths.add(cls._normalize_relative_path(path))
            except ValueError:
                continue
        return paths

    def _sandbox_path(self, relative_path: str) -> str:
        if self._skills_root and (relative_path == "skills" or relative_path.startswith("skills/")):
            skill_relative_path = relative_path.removeprefix("skills").lstrip("/")
            if not skill_relative_path:
                return self._skills_root
            return f"{self._skills_root}/{skill_relative_path}"
        return f"{self._context_root}/{relative_path}"

    def _ensure_parent_dir(self, sandbox: Sandbox, relative_path: str) -> None:
        target_parent = PurePosixPath(self._sandbox_path(relative_path)).parent.as_posix()
        sandbox.create_dir(target_parent, parents=True, exist_ok=True)

    def _prune_stale_files(self, sandbox: Sandbox, existing_payload: dict[str, Any] | None, new_payload: dict[str, Any]) -> None:
        stale_paths = self._manifest_file_paths(existing_payload) - self._manifest_file_paths(new_payload)
        for relative_path in sorted(stale_paths, reverse=True):
            try:
                sandbox.remove_file(self._sandbox_path(relative_path))
            except (FileNotFoundError, OSError):
                continue

    def materialize(self, sandbox: Sandbox, manifest: SandboxMaterializerManifest) -> SandboxMaterializerResult:
        payload = self._manifest_payload(manifest)
        manifest_hash = payload["manifest_hash"]
        existing_payload = self._read_existing_manifest_payload(sandbox, self.manifest_path)
        existing_hash = None if existing_payload is None else existing_payload.get("manifest_hash")
        if existing_hash == manifest_hash:
            return SandboxMaterializerResult(changed=False, manifest_hash=manifest_hash, files_written=0)

        files_written = 0
        sandbox.create_dir(self._context_root, parents=True, exist_ok=True)
        self._prune_stale_files(sandbox, existing_payload, payload)
        for file in manifest.files:
            relative_path = self._normalize_relative_path(file.path)
            self._ensure_parent_dir(sandbox, relative_path)
            target = self._sandbox_path(relative_path)
            if isinstance(file.content, bytes):
                sandbox.update_file(target, file.content)
            else:
                sandbox.write_file(target, file.content)
            files_written += 1

        sandbox.write_file(self.manifest_path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return SandboxMaterializerResult(changed=True, manifest_hash=manifest_hash, files_written=files_written)


class SandboxRuntimeContextManifestBuilder:
    """Build a sandbox runtime-context manifest from configured stores."""

    def __init__(
        self,
        *,
        memory_storage=None,
        agent_store=None,
        skill_storage=None,
    ) -> None:
        self._memory_storage = memory_storage
        self._agent_store = agent_store
        self._skill_storage = skill_storage

    @staticmethod
    def _json_content(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)

    @staticmethod
    def _read_file_content(path: Path) -> str | bytes:
        data = path.read_bytes()
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data

    @staticmethod
    def _skill_relative_prefix(skill) -> str:
        skill_path = skill.skill_path
        if skill_path:
            return f"skills/{skill.category}/{skill_path}"
        return f"skills/{skill.category}"

    @staticmethod
    def _files_revision(files: list[SandboxMaterializedFile]) -> str:
        records = sorted((SandboxMaterializer._file_record(file) for file in files), key=lambda item: item["path"])
        return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    def _iter_skill_files(self, *, skill_names: list[str] | None) -> list[SandboxMaterializedFile]:
        if self._skill_storage is None:
            return []
        requested = set(skill_names) if skill_names is not None else None
        skills = self._skill_storage.load_skills(enabled_only=requested is None)
        files: list[SandboxMaterializedFile] = []
        for skill in skills:
            if requested is not None and skill.name not in requested:
                continue
            prefix = self._skill_relative_prefix(skill)
            file_manifest = self._skill_storage.list_skill_file_manifest(skill.name, skill.category)
            for item in file_manifest:
                files.append(
                    SandboxMaterializedFile(
                        path=f"{prefix}/{item.relative_path}",
                        content=self._skill_storage.read_skill_file(skill.name, skill.category, item.relative_path),
                    )
                )
        return files

    def build(
        self,
        *,
        user_id: str,
        agent_name: str | None = None,
        skill_names: list[str] | None = None,
    ) -> SandboxMaterializerManifest:
        files: list[SandboxMaterializedFile] = []

        revision_parts = [f"user={user_id}"]
        if agent_name:
            revision_parts.append(f"agent={agent_name}")

        if self._memory_storage is not None:
            files.append(
                SandboxMaterializedFile(
                    path="memory/user.json",
                    content=self._json_content(self._memory_storage.load(user_id=user_id)),
                )
            )
            if agent_name:
                files.append(
                    SandboxMaterializedFile(
                        path=f"memory/agents/{agent_name}.json",
                        content=self._json_content(self._memory_storage.load(agent_name, user_id=user_id)),
                    )
                )

        if self._agent_store is not None:
            profile = self._agent_store.load_user_profile(user_id)
            if profile:
                files.append(SandboxMaterializedFile(path="agent/USER.md", content=profile))
            soul = (
                self._agent_store.load_agent_soul(user_id, agent_name)
                if agent_name
                else self._agent_store.load_default_agent_soul(user_id)
            )
            if soul:
                files.append(SandboxMaterializedFile(path="agent/SOUL.md", content=soul))

        files.extend(self._iter_skill_files(skill_names=skill_names))
        if skill_names:
            revision_parts.append(f"skills={','.join(sorted(skill_names))}")
        revision_parts.append(f"snapshot={self._files_revision(files)}")

        return SandboxMaterializerManifest(files=files, revision=";".join(revision_parts))
