import base64
import mimetypes
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from deerflow.agents.thread_state import ThreadDataState
from deerflow.artifacts.store import ArtifactPathError, make_artifact_store, parse_artifact_virtual_path
from deerflow.config.app_config import get_app_config
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

_ALLOWED_IMAGE_VIRTUAL_ROOTS = (
    f"{VIRTUAL_PATH_PREFIX}/workspace",
    f"{VIRTUAL_PATH_PREFIX}/uploads",
    f"{VIRTUAL_PATH_PREFIX}/outputs",
)
_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT = ", ".join(_ALLOWED_IMAGE_VIRTUAL_ROOTS)
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_EXTENSION_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _is_allowed_image_virtual_path(image_path: str) -> bool:
    return any(image_path == root or image_path.startswith(f"{root}/") for root in _ALLOWED_IMAGE_VIRTUAL_ROOTS)


def _detect_image_mime(image_data: bytes) -> str | None:
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(image_data) >= 12 and image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _sanitize_image_error(error: Exception, thread_data: ThreadDataState | None) -> str:
    from deerflow.sandbox.tools import mask_local_paths_in_output

    return mask_local_paths_in_output(f"{type(error).__name__}: {error}", thread_data)


def _runtime_storage_is_object() -> bool:
    return get_app_config().runtime_storage.backend == "object"


def _get_thread_id(runtime: Runtime) -> str | None:
    context = getattr(runtime, "context", None)
    if isinstance(context, dict) and context.get("thread_id"):
        return str(context["thread_id"])
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        thread_id = config.get("configurable", {}).get("thread_id")
        if thread_id:
            return str(thread_id)
    return None


def _view_image_command_from_bytes(
    *,
    image_path: str,
    image_data: bytes,
    expected_mime_type: str,
    tool_call_id: str,
) -> Command:
    detected_mime_type = _detect_image_mime(image_data)
    if detected_mime_type is None:
        return Command(
            update={"messages": [ToolMessage("Error: File contents do not match a supported image format", tool_call_id=tool_call_id)]},
        )
    if detected_mime_type != expected_mime_type:
        return Command(
            update={"messages": [ToolMessage(f"Error: Image contents are {detected_mime_type}, but file extension indicates {expected_mime_type}", tool_call_id=tool_call_id)]},
        )

    image_base64 = base64.b64encode(image_data).decode("utf-8")
    new_viewed_images = {image_path: {"base64": image_base64, "mime_type": detected_mime_type}}
    return Command(
        update={"viewed_images": new_viewed_images, "messages": [ToolMessage("Successfully read image", tool_call_id=tool_call_id)]},
    )


@tool("view_image", parse_docstring=True)
def view_image_tool(
    runtime: Runtime,
    image_path: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Read an image file.

    Use this tool to read an image file and make it available for display.

    When to use the view_image tool:
    - When you need to view an image file.

    When NOT to use the view_image tool:
    - For non-image files (use present_files instead)
    - For multiple files at once (use present_files instead)

    Args:
        image_path: Absolute /mnt/user-data virtual path to the image file. Common formats supported: jpg, jpeg, png, webp.
    """
    from deerflow.sandbox.exceptions import SandboxRuntimeError
    from deerflow.sandbox.tools import (
        get_thread_data,
        resolve_and_validate_user_data_path,
        validate_local_tool_path,
    )

    thread_data = get_thread_data(runtime)

    if not _is_allowed_image_virtual_path(image_path):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"Error: Only image paths under {_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT} are allowed",
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    expected_mime_type = _EXTENSION_TO_MIME.get(Path(image_path).suffix.lower())
    if expected_mime_type is None:
        return Command(
            update={"messages": [ToolMessage(f"Error: Unsupported image format: {Path(image_path).suffix}. Supported formats: {', '.join(_EXTENSION_TO_MIME)}", tool_call_id=tool_call_id)]},
        )

    if _runtime_storage_is_object():
        try:
            parsed = parse_artifact_virtual_path(image_path)
        except ArtifactPathError as e:
            return Command(
                update={"messages": [ToolMessage(f"Error: {str(e)}", tool_call_id=tool_call_id)]},
            )
        thread_id = _get_thread_id(runtime)
        if not thread_id:
            return Command(
                update={"messages": [ToolMessage("Error: Thread ID is not available in runtime context or runtime config", tool_call_id=tool_call_id)]},
            )
        config = get_app_config()
        artifact_store = make_artifact_store(config.runtime_storage)
        if artifact_store is None:
            return Command(
                update={"messages": [ToolMessage("Error: Runtime artifact store is not configured", tool_call_id=tool_call_id)]},
            )
        try:
            image_data = artifact_store.get_bytes(resolve_runtime_user_id(runtime), thread_id, parsed.virtual_path)
        except FileNotFoundError:
            return Command(
                update={"messages": [ToolMessage(f"Error: Image file not found: {image_path}", tool_call_id=tool_call_id)]},
            )
        except Exception as e:
            return Command(
                update={"messages": [ToolMessage(f"Error reading image file: {type(e).__name__}: {e}", tool_call_id=tool_call_id)]},
            )
        if len(image_data) > _MAX_IMAGE_BYTES:
            return Command(
                update={"messages": [ToolMessage(f"Error: Image file is too large: {len(image_data)} bytes. Maximum supported size is {_MAX_IMAGE_BYTES} bytes", tool_call_id=tool_call_id)]},
            )
        return _view_image_command_from_bytes(
            image_path=parsed.virtual_path,
            image_data=image_data,
            expected_mime_type=expected_mime_type,
            tool_call_id=tool_call_id,
        )

    try:
        validate_local_tool_path(image_path, thread_data, read_only=True)
        actual_path = resolve_and_validate_user_data_path(image_path, thread_data)
    except (PermissionError, SandboxRuntimeError) as e:
        return Command(
            update={"messages": [ToolMessage(f"Error: {str(e)}", tool_call_id=tool_call_id)]},
        )

    path = Path(actual_path)

    # Validate that the file exists
    if not path.exists():
        return Command(
            update={"messages": [ToolMessage(f"Error: Image file not found: {image_path}", tool_call_id=tool_call_id)]},
        )

    # Validate that it's a file (not a directory)
    if not path.is_file():
        return Command(
            update={"messages": [ToolMessage(f"Error: Path is not a file: {image_path}", tool_call_id=tool_call_id)]},
        )

    # Detect MIME type from file extension
    mime_type, _ = mimetypes.guess_type(actual_path)
    if mime_type is None:
        mime_type = expected_mime_type

    try:
        image_size = path.stat().st_size
    except OSError as e:
        return Command(
            update={"messages": [ToolMessage(f"Error reading image metadata: {_sanitize_image_error(e, thread_data)}", tool_call_id=tool_call_id)]},
        )
    if image_size > _MAX_IMAGE_BYTES:
        return Command(
            update={"messages": [ToolMessage(f"Error: Image file is too large: {image_size} bytes. Maximum supported size is {_MAX_IMAGE_BYTES} bytes", tool_call_id=tool_call_id)]},
        )

    # Read image file and convert to base64
    try:
        with open(actual_path, "rb") as f:
            image_data = f.read()
    except Exception as e:
        return Command(
            update={"messages": [ToolMessage(f"Error reading image file: {_sanitize_image_error(e, thread_data)}", tool_call_id=tool_call_id)]},
        )

    return _view_image_command_from_bytes(
        image_path=image_path,
        image_data=image_data,
        expected_mime_type=expected_mime_type,
        tool_call_id=tool_call_id,
    )
