"""Codex-style patch parsing and application helpers."""

from __future__ import annotations

from dataclasses import dataclass, field


class PatchError(ValueError):
    """Raised when a patch cannot be parsed or applied."""


@dataclass(frozen=True)
class PatchLine:
    op: str
    text: str


@dataclass(frozen=True)
class PatchChunk:
    lines: tuple[PatchLine, ...] = ()
    context: str | None = None
    end_of_file: bool = False


@dataclass(frozen=True)
class PatchOperation:
    kind: str
    path: str
    lines: tuple[str, ...] = ()
    chunks: tuple[PatchChunk, ...] = ()
    move_path: str | None = None


@dataclass
class PatchSummary:
    added: int = 0
    updated: int = 0
    deleted: int = 0
    moved: int = 0
    paths: set[str] = field(default_factory=set)


_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_DELETE = "*** Delete File: "
_UPDATE = "*** Update File: "
_MOVE = "*** Move to: "
_EOF = "*** End of File"


def parse_patch(patch_text: str) -> list[PatchOperation]:
    """Parse a Codex-style patch into operations."""
    lines = patch_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines or lines[0].strip() != _BEGIN:
        raise PatchError("Patch must start with '*** Begin Patch'.")
    if lines[-1].strip() != _END:
        raise PatchError("Patch must end with '*** End Patch'.")

    operations: list[PatchOperation] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("*** Environment ID: "):
            index += 1
            continue
        if line.startswith(_ADD):
            path = line[len(_ADD) :].strip()
            index += 1
            add_lines: list[str] = []
            while index < len(lines) - 1 and (not lines[index].startswith("*** ") or lines[index].strip() == _EOF):
                current = lines[index]
                if not current.startswith("+"):
                    raise PatchError(f"Add File lines must start with '+': {current!r}")
                add_lines.append(current[1:])
                index += 1
            operations.append(PatchOperation(kind="add", path=path, lines=tuple(add_lines)))
            continue
        if line.startswith(_DELETE):
            path = line[len(_DELETE) :].strip()
            operations.append(PatchOperation(kind="delete", path=path))
            index += 1
            continue
        if line.startswith(_UPDATE):
            path = line[len(_UPDATE) :].strip()
            index += 1
            move_path: str | None = None
            if index < len(lines) - 1 and lines[index].strip().startswith(_MOVE):
                move_path = lines[index].strip()[len(_MOVE) :].strip()
                index += 1

            chunks: list[PatchChunk] = []
            current_chunk: list[PatchLine] = []
            current_context: str | None = None
            current_end_of_file = False

            def flush_chunk() -> None:
                nonlocal current_chunk, current_context, current_end_of_file
                if current_chunk or current_end_of_file:
                    chunks.append(
                        PatchChunk(
                            lines=tuple(current_chunk),
                            context=current_context,
                            end_of_file=current_end_of_file,
                        )
                    )
                current_chunk = []
                current_context = None
                current_end_of_file = False

            while index < len(lines) - 1 and (not lines[index].startswith("*** ") or lines[index].strip() == _EOF):
                current = lines[index]
                stripped = current.strip()
                if stripped == _EOF:
                    current_end_of_file = True
                    index += 1
                    continue
                if current.startswith("@@"):
                    flush_chunk()
                    context = current[2:].strip()
                    current_context = context or None
                    index += 1
                    continue
                if not current:
                    raise PatchError("Empty patch lines must be prefixed with ' ', '+', or '-'.")
                prefix = current[0]
                if prefix not in {" ", "+", "-"}:
                    raise PatchError(f"Update lines must start with ' ', '+', or '-': {current!r}")
                current_chunk.append(PatchLine(prefix, current[1:]))
                index += 1
            flush_chunk()
            if not chunks and move_path is None:
                raise PatchError(f"Update File has no changes: {path}")
            operations.append(PatchOperation(kind="update", path=path, chunks=tuple(chunks), move_path=move_path))
            continue
        raise PatchError(f"Unsupported patch line: {lines[index]!r}")

    if not operations:
        raise PatchError("Patch contains no file operations.")
    return operations


def build_added_content(lines: tuple[str, ...]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def derive_updated_content(original: str, chunks: tuple[PatchChunk, ...]) -> str:
    """Apply update chunks to *original* using exact context matching."""
    if not chunks:
        return original

    original_lines = original.splitlines()
    had_trailing_newline = original.endswith(("\n", "\r\n"))
    result: list[str] = []
    cursor = 0

    for chunk in chunks:
        old_lines = [line.text for line in chunk.lines if line.op in {" ", "-"}]
        new_lines = [line.text for line in chunk.lines if line.op in {" ", "+"}]
        if old_lines:
            match_index = _find_sequence(original_lines, old_lines, cursor)
            if match_index is None:
                preview = "\n".join(old_lines[:3])
                raise PatchError(f"Patch context not found: {preview}")
            result.extend(original_lines[cursor:match_index])
            result.extend(new_lines)
            cursor = match_index + len(old_lines)
            continue

        if chunk.context is not None:
            match_index = _find_sequence(original_lines, [chunk.context], cursor)
            if match_index is None:
                raise PatchError(f"Patch context not found: {chunk.context}")
            insert_index = match_index + 1
        elif chunk.end_of_file:
            insert_index = len(original_lines)
        else:
            raise PatchError("Pure insertion requires @@ context or *** End of File.")

        result.extend(original_lines[cursor:insert_index])
        result.extend(new_lines)
        cursor = insert_index

    result.extend(original_lines[cursor:])
    updated = "\n".join(result)
    if had_trailing_newline and result:
        updated += "\n"
    return updated


def _find_sequence(lines: list[str], sequence: list[str], start: int) -> int | None:
    if not sequence:
        return start
    max_start = len(lines) - len(sequence)
    for index in range(start, max_start + 1):
        if lines[index : index + len(sequence)] == sequence:
            return index
    return None
