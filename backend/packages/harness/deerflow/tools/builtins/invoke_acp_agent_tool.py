"""Built-in tool for invoking external ACP-compatible agents."""

import asyncio
import logging
import mimetypes
import os
import shlex
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolArg, StructuredTool
from pydantic import BaseModel, Field

from deerflow.artifacts.sandbox_materializer import SandboxArtifactMaterializer
from deerflow.artifacts.store import ACP_WORKSPACE_ROOT, ArtifactStore, make_artifact_store
from deerflow.config import get_app_config
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox import get_sandbox_provider
from deerflow.sandbox.sandbox import Sandbox

logger = logging.getLogger(__name__)

ACP_OBJECT_THREAD_ID_ERROR = "object-backed ACP workspace requires thread_id for durable artifact ownership"


class _InvokeACPAgentInput(BaseModel):
    agent: str = Field(description="Name of the ACP agent to invoke")
    prompt: str = Field(description="The concise task prompt to send to the agent")


def _get_work_dir(thread_id: str | None) -> str:
    """Get the per-thread ACP workspace directory.

    Each thread gets an isolated workspace under
    ``{base_dir}/threads/{thread_id}/acp-workspace/`` so that concurrent
    sessions cannot read or overwrite each other's ACP agent outputs.

    Falls back to the legacy global ``{base_dir}/acp-workspace/`` when
    ``thread_id`` is not available (e.g. embedded / direct invocation).

    The directory is created automatically if it does not exist.

    Returns:
        An absolute physical filesystem path to use as the working directory.
    """
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    paths = get_paths()
    if thread_id:
        try:
            work_dir = paths.acp_workspace_dir(thread_id, user_id=get_effective_user_id())
        except ValueError:
            logger.warning("Invalid thread_id %r for ACP workspace, falling back to global", thread_id)
            work_dir = paths.base_dir / "acp-workspace"
    else:
        work_dir = paths.base_dir / "acp-workspace"

    work_dir.mkdir(parents=True, exist_ok=True)
    logger.info("ACP agent work_dir: %s", work_dir)
    return str(work_dir)


def _object_runtime_storage_enabled() -> bool:
    try:
        return get_app_config().runtime_storage.backend == "object"
    except Exception:
        return False


@contextmanager
def _acp_workspace_context(thread_id: str | None) -> Iterator[str]:
    if not thread_id or not _object_runtime_storage_enabled():
        yield _get_work_dir(thread_id)
        return

    artifact_store = make_artifact_store(get_app_config().runtime_storage)
    if artifact_store is None:
        yield _get_work_dir(thread_id)
        return

    user_id = str(get_effective_user_id())
    with tempfile.TemporaryDirectory(prefix="deerflow-acp-") as temp_dir:
        work_dir = Path(temp_dir)
        _materialize_acp_workspace(artifact_store, user_id, thread_id, work_dir)
        logger.info("ACP agent object-backed staging work_dir: %s", work_dir)
        try:
            yield str(work_dir)
        finally:
            _flush_acp_workspace(artifact_store, user_id, thread_id, work_dir)


def _materialize_acp_workspace(artifact_store: ArtifactStore, user_id: str, thread_id: str, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    for item in artifact_store.list_files(user_id, thread_id, ACP_WORKSPACE_ROOT):
        relative = _acp_virtual_relative_path(item.virtual_path)
        local_path = _safe_local_acp_path(work_dir, relative)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(artifact_store.get_bytes(user_id, thread_id, item.virtual_path))


def _flush_acp_workspace(artifact_store: ArtifactStore, user_id: str, thread_id: str, work_dir: Path) -> None:
    store_paths = {item.virtual_path for item in artifact_store.list_files(user_id, thread_id, ACP_WORKSPACE_ROOT)}
    local_paths: set[str] = set()

    for local_path in sorted(work_dir.rglob("*")):
        if local_path.is_symlink() or not local_path.is_file():
            continue
        relative = local_path.relative_to(work_dir).as_posix()
        virtual_path = f"{ACP_WORKSPACE_ROOT}/{relative}"
        content_type, _ = mimetypes.guess_type(virtual_path)
        artifact_store.put_bytes(
            user_id,
            thread_id,
            virtual_path,
            local_path.read_bytes(),
            content_type=content_type,
        )
        local_paths.add(virtual_path)

    for stale_path in sorted(store_paths - local_paths):
        artifact_store.delete(user_id, thread_id, stale_path)


def _acp_virtual_relative_path(virtual_path: str) -> str:
    relative = virtual_path.removeprefix(f"{ACP_WORKSPACE_ROOT}/")
    if not relative or relative == virtual_path or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError(f"Invalid ACP workspace object path: {virtual_path}")
    return relative


def _safe_local_acp_path(work_dir: Path, relative_path: str) -> Path:
    local_path = (work_dir / relative_path).resolve()
    work_root = work_dir.resolve()
    if local_path != work_root and work_root not in local_path.parents:
        raise ValueError(f"ACP workspace object path escapes staging directory: {relative_path}")
    return local_path


def _acp_sandbox_materializer() -> tuple[ArtifactStore, SandboxArtifactMaterializer] | None:
    try:
        runtime_storage = get_app_config().runtime_storage
    except Exception:
        return None
    if runtime_storage.backend != "object":
        return None
    artifact_store = make_artifact_store(runtime_storage)
    if artifact_store is None:
        return None
    object_store_config = runtime_storage.object_store
    materializer_kwargs = {}
    if object_store_config is not None:
        materializer_kwargs = {
            "max_materialize_files": object_store_config.max_materialize_files,
            "max_materialize_bytes": object_store_config.max_materialize_bytes,
        }
    return artifact_store, SandboxArtifactMaterializer(artifact_store, **materializer_kwargs)


def _refresh_active_leader_acp_workspace(provider: Any, thread_id: str | None) -> None:
    if not thread_id:
        return

    refresh_thread_artifacts = getattr(provider, "refresh_thread_artifacts", None)
    if refresh_thread_artifacts is None:
        return

    try:
        refreshed = refresh_thread_artifacts(thread_id, roots=(ACP_WORKSPACE_ROOT,))
    except Exception as exc:
        raise RuntimeError(f"failed to refresh leader sandbox ACP workspace after ACP flush: {exc}") from exc

    if refreshed:
        logger.info("Refreshed active leader sandbox ACP workspace for thread %s", thread_id)


def _build_mcp_servers() -> dict[str, dict[str, Any]]:
    """Build ACP ``mcpServers`` config from DeerFlow's enabled MCP servers."""
    from deerflow.config.extensions_config import reload_extensions_config
    from deerflow.mcp.client import build_servers_config

    return build_servers_config(reload_extensions_config())


def _build_acp_mcp_servers() -> list[dict[str, Any]]:
    """Build ACP ``mcpServers`` payload for ``new_session``.

    The ACP client expects a list of server objects, while DeerFlow's MCP helper
    returns a name -> config mapping for the LangChain MCP adapter. This helper
    converts the enabled servers into the ACP wire format.
    """
    from deerflow.config.extensions_config import reload_extensions_config

    extensions_config = reload_extensions_config()
    enabled_servers = extensions_config.get_enabled_mcp_servers()

    mcp_servers: list[dict[str, Any]] = []
    for name, server_config in enabled_servers.items():
        transport_type = server_config.type or "stdio"
        payload: dict[str, Any] = {"name": name, "type": transport_type}

        if transport_type == "stdio":
            if not server_config.command:
                raise ValueError(f"MCP server '{name}' with stdio transport requires 'command' field")
            payload["command"] = server_config.command
            payload["args"] = server_config.args
            payload["env"] = [{"name": key, "value": value} for key, value in server_config.env.items()]
        elif transport_type in ("http", "sse"):
            if not server_config.url:
                raise ValueError(f"MCP server '{name}' with {transport_type} transport requires 'url' field")
            payload["url"] = server_config.url
            payload["headers"] = [{"name": key, "value": value} for key, value in server_config.headers.items()]
        else:
            raise ValueError(f"MCP server '{name}' has unsupported transport type: {transport_type}")

        mcp_servers.append(payload)

    return mcp_servers


class _SandboxBashWriter:
    def __init__(self, transport: "_SandboxBashTransport") -> None:
        self._transport = transport
        self._buffer = bytearray()
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("ACP sandbox transport writer is closed")
        self._buffer.extend(data)

    async def drain(self) -> None:
        if not self._buffer:
            return
        data = bytes(self._buffer)
        self._buffer.clear()
        await self._transport.write_stdin(data)

    def close(self) -> None:
        self._closed = True


class _SandboxBashTransport:
    def __init__(
        self,
        sandbox: Sandbox,
        command: str,
        args: tuple[str, ...],
        *,
        env: dict[str, str] | None,
        cwd: str,
    ) -> None:
        self._sandbox = sandbox
        self._command = command
        self._args = args
        self._env = env
        self._cwd = cwd
        self.reader = asyncio.StreamReader()
        self.writer = _SandboxBashWriter(self)
        self._session_id = f"acp-{uuid.uuid4().hex}"
        self._command_id: str | None = None
        self._offset = 0
        self._stderr_offset = 0
        self._poll_task: asyncio.Task[None] | None = None
        self._closed = False
        self._finished = False

    async def start(self) -> None:
        client = getattr(self._sandbox, "_client", None)
        if client is None or not hasattr(client, "bash"):
            raise RuntimeError("sandbox-native ACP requires an AIO sandbox with bash session API")
        self._bash = client.bash
        await asyncio.to_thread(self._bash.create_session, session_id=self._session_id, exec_dir=self._cwd)
        result = await asyncio.to_thread(
            self._bash.exec,
            command=self._command_line(),
            session_id=self._session_id,
            exec_dir=self._cwd,
            env=self._env,
            async_mode=True,
            max_output_length=0,
        )
        self._command_id = str(result.command_id)
        self._offset = int(result.offset or 0)
        self._stderr_offset = int(result.stderr_offset or 0)
        if result.stdout:
            self.reader.feed_data(result.stdout.encode("utf-8"))
        if result.stderr:
            logger.debug("ACP sandbox stderr during start: %s", result.stderr)
        self._poll_task = asyncio.create_task(self._poll_output(), name=f"acp-sandbox-output-{self._session_id}")

    def _command_line(self) -> str:
        return " ".join(shlex.quote(part) for part in (self._command, *self._args))

    async def write_stdin(self, data: bytes) -> None:
        if self._command_id is None:
            raise RuntimeError("ACP sandbox command has not started")
        await asyncio.to_thread(
            self._bash.write,
            session_id=self._session_id,
            command_id=self._command_id,
            input=data.decode("utf-8"),
        )

    async def _poll_output(self) -> None:
        try:
            while not self._closed:
                assert self._command_id is not None
                result = await asyncio.to_thread(
                    self._bash.output,
                    session_id=self._session_id,
                    command_id=self._command_id,
                    offset=self._offset,
                    stderr_offset=self._stderr_offset,
                    wait=True,
                    wait_timeout=0.5,
                )
                if result.stdout:
                    self.reader.feed_data(result.stdout.encode("utf-8"))
                if result.stderr:
                    logger.debug("ACP sandbox stderr: %s", result.stderr)
                self._offset = int(result.offset if result.offset is not None else self._offset)
                self._stderr_offset = int(result.stderr_offset if result.stderr_offset is not None else self._stderr_offset)
                command_info = getattr(result, "command", None)
                status = str(getattr(command_info, "status", "") or "").lower()
                if status in {"completed", "timed_out", "killed"}:
                    self._finished = True
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ACP sandbox output polling failed: %s", exc)
        finally:
            self.reader.feed_eof()

    async def close(self) -> None:
        self._closed = True
        self.writer.close()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if not self._finished:
            try:
                await asyncio.to_thread(self._bash.kill, session_id=self._session_id, signal="SIGTERM")
            except Exception as exc:
                logger.debug("Failed to terminate ACP sandbox bash session %s: %s", self._session_id, exc)
        try:
            await asyncio.to_thread(self._bash.close_session, self._session_id)
        except Exception as exc:
            logger.debug("Failed to close ACP sandbox bash session %s: %s", self._session_id, exc)


@asynccontextmanager
async def _spawn_sandbox_agent_process(
    client,
    sandbox: Sandbox,
    command: str,
    *args: str,
    env: dict[str, str] | None = None,
    cwd: str = ACP_WORKSPACE_ROOT,
    **connection_kwargs: Any,
):
    from acp import connect_to_agent

    transport = _SandboxBashTransport(sandbox, command, args, env=env, cwd=cwd)
    await transport.start()
    conn = connect_to_agent(client, transport.writer, transport.reader, **connection_kwargs)
    try:
        yield conn, transport
    finally:
        await conn.close()
        await transport.close()


@asynccontextmanager
async def _open_acp_agent_process(
    agent_name: str,
    agent_config,
    client,
    cmd: str,
    args: list[str],
    *,
    env: dict[str, str] | None,
    thread_id: str | None,
) -> AsyncIterator[tuple[Any, Any, str]]:
    if agent_config.execution_mode == "sandbox":
        if thread_id is None and _object_runtime_storage_enabled():
            raise RuntimeError(ACP_OBJECT_THREAD_ID_ERROR)

        provider = get_sandbox_provider()
        if not hasattr(provider, "acquire_ephemeral"):
            raise RuntimeError("ACP sandbox execution requires a sandbox provider with acquire_ephemeral()")
        profile = agent_config.sandbox_profile or agent_name
        sandbox_id = provider.acquire_ephemeral(agent_name, profile=profile)
        try:
            sandbox = provider.get(sandbox_id)
            if sandbox is None:
                raise RuntimeError(f"ACP sandbox {sandbox_id} was acquired but could not be found")
            materializer_entry = _acp_sandbox_materializer() if thread_id else None
            if materializer_entry is not None:
                user_id = str(get_effective_user_id())
                _, materializer = materializer_entry
                materializer.materialize_thread(user_id, thread_id, sandbox, roots=(ACP_WORKSPACE_ROOT,))
            elif hasattr(sandbox, "create_dir"):
                sandbox.create_dir(ACP_WORKSPACE_ROOT, parents=True, exist_ok=True)
            try:
                async with _spawn_sandbox_agent_process(client, sandbox, cmd, *args, env=env, cwd=ACP_WORKSPACE_ROOT) as (conn, proc):
                    yield conn, proc, ACP_WORKSPACE_ROOT
            finally:
                if materializer_entry is not None:
                    materializer.flush_thread(user_id, thread_id, sandbox, roots=(ACP_WORKSPACE_ROOT,))
                    _refresh_active_leader_acp_workspace(provider, thread_id)
        finally:
            provider.release(sandbox_id)
        return

    from acp import spawn_agent_process

    with _acp_workspace_context(thread_id) as physical_cwd:
        async with spawn_agent_process(client, cmd, *args, env=env, cwd=physical_cwd) as (conn, proc):
            yield conn, proc, physical_cwd


def _build_permission_response(options: list[Any], *, auto_approve: bool) -> Any:
    """Build an ACP permission response.

    When ``auto_approve`` is True, selects the first ``allow_once`` (preferred)
    or ``allow_always`` option.  When False (the default), always cancels —
    permission requests must be handled by the ACP agent's own policy or the
    agent must be configured to operate without requesting permissions.
    """
    from acp import RequestPermissionResponse
    from acp.schema import AllowedOutcome, DeniedOutcome

    if auto_approve:
        for preferred_kind in ("allow_once", "allow_always"):
            for option in options:
                if getattr(option, "kind", None) != preferred_kind:
                    continue

                option_id = getattr(option, "option_id", None)
                if option_id is None:
                    option_id = getattr(option, "optionId", None)
                if option_id is None:
                    continue

                return RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", optionId=option_id),
                )

    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def _format_invocation_error(agent: str, cmd: str, exc: Exception) -> str:
    """Return a user-facing ACP invocation error with actionable remediation."""
    if not isinstance(exc, FileNotFoundError):
        return f"Error invoking ACP agent '{agent}': {exc}"

    message = f"Error invoking ACP agent '{agent}': Command '{cmd}' was not found on PATH."
    if cmd == "codex-acp" and shutil.which("codex"):
        return f"{message} The installed `codex` CLI does not speak ACP directly. Install a Codex ACP adapter (for example `npx @zed-industries/codex-acp`) or update `acp_agents.codex.command` and `args` in config.yaml."

    return f"{message} Install the agent binary or update `acp_agents.{agent}.command` in config.yaml."


def build_invoke_acp_agent_tool(agents: dict) -> BaseTool:
    """Create the ``invoke_acp_agent`` tool with a description generated from configured agents.

    The tool description includes the list of available agents so that the LLM
    knows which agents it can invoke without requiring hardcoded names.

    Args:
        agents: Mapping of agent name -> ``ACPAgentConfig``.

    Returns:
        A LangChain ``BaseTool`` ready to be included in the tool list.
    """
    agent_lines = "\n".join(f"- {name}: {cfg.description}" for name, cfg in agents.items())
    description = (
        "Invoke an external ACP-compatible agent and return its final response.\n\n"
        "Available agents:\n"
        f"{agent_lines}\n\n"
        "IMPORTANT: ACP agents operate in their own independent workspace. "
        "Do NOT include /mnt/user-data paths in the prompt. "
        "Give the agent a self-contained task description — it will produce results in its own workspace. "
        "After the agent completes, its output files are accessible at /mnt/acp-workspace/ (read-only)."
    )

    # Capture agents in closure so the function can reference it
    _agents = dict(agents)

    async def _invoke(agent: str, prompt: str, config: Annotated[RunnableConfig, InjectedToolArg] = None) -> str:
        logger.info("Invoking ACP agent %s (prompt length: %d)", agent, len(prompt))
        logger.debug("Invoking ACP agent %s with prompt: %.200s%s", agent, prompt, "..." if len(prompt) > 200 else "")
        if agent not in _agents:
            available = ", ".join(_agents.keys())
            return f"Error: Unknown agent '{agent}'. Available: {available}"

        agent_config = _agents[agent]
        thread_id: str | None = ((config or {}).get("configurable") or {}).get("thread_id")

        try:
            from acp import PROTOCOL_VERSION, Client, text_block
            from acp.schema import ClientCapabilities, Implementation
        except ImportError:
            return "Error: agent-client-protocol package is not installed. Run `uv sync` to install project dependencies."

        class _CollectingClient(Client):
            """Minimal ACP Client that collects streamed text from session updates."""

            def __init__(self) -> None:
                self._chunks: list[str] = []

            @property
            def collected_text(self) -> str:
                return "".join(self._chunks)

            async def session_update(self, session_id: str, update, **kwargs) -> None:  # type: ignore[override]
                try:
                    from acp.schema import TextContentBlock

                    if hasattr(update, "content") and isinstance(update.content, TextContentBlock):
                        self._chunks.append(update.content.text)
                except Exception:
                    pass

            async def request_permission(self, options, session_id: str, tool_call, **kwargs):  # type: ignore[override]
                response = _build_permission_response(options, auto_approve=agent_config.auto_approve_permissions)
                outcome = response.outcome.outcome
                if outcome == "selected":
                    logger.info("ACP permission auto-approved for tool call %s in session %s", tool_call.tool_call_id, session_id)
                else:
                    logger.warning("ACP permission denied for tool call %s in session %s (set auto_approve_permissions: true in config.yaml to enable)", tool_call.tool_call_id, session_id)
                return response

        client = _CollectingClient()
        cmd = agent_config.command
        args = agent_config.args or []
        try:
            mcp_servers = _build_acp_mcp_servers()
        except ValueError as exc:
            logger.warning(
                "Invalid MCP server configuration for ACP agent '%s'; continuing without MCP servers: %s",
                agent,
                exc,
            )
            mcp_servers = []
        agent_env: dict[str, str] | None = None
        if agent_config.env:
            agent_env = {k: (os.environ.get(v[1:], "") if v.startswith("$") else v) for k, v in agent_config.env.items()}

        try:
            async with _open_acp_agent_process(
                agent,
                agent_config,
                client,
                cmd,
                args,
                env=agent_env,
                thread_id=thread_id,
            ) as (conn, proc, physical_cwd):
                logger.info("Spawning ACP agent '%s' with command '%s' and args %s in cwd %s", agent, cmd, args, physical_cwd)
                await conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(),
                    client_info=Implementation(name="deerflow", title="DeerFlow", version="0.1.0"),
                )
                session_kwargs: dict[str, Any] = {"cwd": physical_cwd, "mcp_servers": mcp_servers}
                if agent_config.model:
                    session_kwargs["model"] = agent_config.model
                session = await conn.new_session(**session_kwargs)
                await conn.prompt(
                    session_id=session.session_id,
                    prompt=[text_block(prompt)],
                )
            result = client.collected_text
            logger.info("ACP agent '%s' returned %s", agent, result[:1000])
            logger.info("ACP agent '%s' returned %d characters", agent, len(result))
            return result or "(no response)"
        except Exception as e:
            logger.error("ACP agent '%s' invocation failed: %s", agent, e)
            return _format_invocation_error(agent, cmd, e)

    return StructuredTool.from_function(
        name="invoke_acp_agent",
        description=description,
        coroutine=_invoke,
        args_schema=_InvokeACPAgentInput,
    )
