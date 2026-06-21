"""Import filesystem runtime artifacts into object storage.

This migrates the runtime PVC/.deer-flow file contract:

- users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}/...
- users/{user_id}/threads/{thread_id}/acp-workspace/...
- legacy threads/{thread_id}/... as user_id=default
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deerflow.artifacts.store import ACP_WORKSPACE_ROOT, USER_DATA_ROOT, ArtifactStore, make_artifact_store
from deerflow.config.app_config import get_app_config
from deerflow.runtime.user_context import DEFAULT_USER_ID


@dataclass(frozen=True)
class RuntimeArtifactCandidate:
    user_id: str
    thread_id: str
    source_path: Path
    virtual_path: str


def iter_runtime_artifacts(state_dir: Path) -> Iterable[RuntimeArtifactCandidate]:
    state_dir = state_dir.expanduser().resolve()
    yield from _iter_user_thread_artifacts(state_dir / "users")
    yield from _iter_threads_root(state_dir / "threads", user_id=DEFAULT_USER_ID)


def import_runtime_artifacts(
    *,
    state_dir: Path,
    artifact_store: ArtifactStore,
    dry_run: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "state_dir": str(state_dir),
        "dry_run": dry_run,
        "discovered": 0,
        "imported": 0,
        "skipped": 0,
        "errors": [],
    }

    for candidate in iter_runtime_artifacts(state_dir):
        report["discovered"] += 1
        if candidate.source_path.is_symlink() or not candidate.source_path.is_file():
            report["skipped"] += 1
            continue
        if dry_run:
            continue
        try:
            data = candidate.source_path.read_bytes()
            content_type, _ = mimetypes.guess_type(candidate.source_path.name)
            artifact_store.put_bytes(
                candidate.user_id,
                candidate.thread_id,
                candidate.virtual_path,
                data,
                content_type=content_type,
            )
            report["imported"] += 1
        except Exception as exc:  # pragma: no cover - defensive reporting path
            report["errors"].append(
                {
                    "source_path": str(candidate.source_path),
                    "virtual_path": candidate.virtual_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return report


def _iter_user_thread_artifacts(users_root: Path) -> Iterable[RuntimeArtifactCandidate]:
    if not users_root.exists():
        return
    for user_dir in sorted(path for path in users_root.iterdir() if path.is_dir()):
        yield from _iter_threads_root(user_dir / "threads", user_id=user_dir.name)


def _iter_threads_root(threads_root: Path, *, user_id: str) -> Iterable[RuntimeArtifactCandidate]:
    if not threads_root.exists():
        return
    for thread_dir in sorted(path for path in threads_root.iterdir() if path.is_dir()):
        yield from _iter_thread_artifacts(user_id=user_id, thread_id=thread_dir.name, thread_dir=thread_dir)


def _iter_thread_artifacts(*, user_id: str, thread_id: str, thread_dir: Path) -> Iterable[RuntimeArtifactCandidate]:
    user_data = thread_dir / "user-data"
    for scope in ("workspace", "uploads", "outputs"):
        root = user_data / scope
        if root.exists():
            yield from _iter_files_under_root(
                user_id=user_id,
                thread_id=thread_id,
                root=root,
                virtual_root=f"{USER_DATA_ROOT}/{scope}",
            )

    acp_workspace = thread_dir / "acp-workspace"
    if acp_workspace.exists():
        yield from _iter_files_under_root(
            user_id=user_id,
            thread_id=thread_id,
            root=acp_workspace,
            virtual_root=ACP_WORKSPACE_ROOT,
        )


def _iter_files_under_root(*, user_id: str, thread_id: str, root: Path, virtual_root: str) -> Iterable[RuntimeArtifactCandidate]:
    for source_path in sorted(root.rglob("*")):
        if not source_path.is_file() and not source_path.is_symlink():
            continue
        relative = source_path.relative_to(root).as_posix()
        yield RuntimeArtifactCandidate(
            user_id=user_id,
            thread_id=thread_id,
            source_path=source_path,
            virtual_path=f"{virtual_root}/{relative}",
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import runtime PVC/.deer-flow artifacts into configured object storage.")
    parser.add_argument("--state-dir", type=Path, default=None, help="Path to .deer-flow state dir. Defaults to configured DEER_FLOW_HOME.")
    parser.add_argument("--dry-run", action="store_true", help="Discover files without writing object storage.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    config = get_app_config()
    artifact_store = make_artifact_store(config.runtime_storage)
    if artifact_store is None:
        parser.error("runtime_storage.backend must be object")

    state_dir = args.state_dir
    if state_dir is None:
        from deerflow.config.paths import get_paths

        state_dir = get_paths().base_dir

    report = import_runtime_artifacts(state_dir=state_dir, artifact_store=artifact_store, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"discovered={report['discovered']} imported={report['imported']} skipped={report['skipped']} errors={len(report['errors'])}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
