import pytest
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
from deerflow.config.app_config import AppConfig


def _as_posix(path: str) -> str:
    return path.replace("\\", "/")


def _object_runtime_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "database": {"backend": "postgres", "postgres_url": "postgresql://user:pass@db/deerflow"},
            "runtime_storage": {
                "backend": "object",
                "object_store": {
                    "endpoint_url": "http://seaweedfs:8333",
                    "bucket": "deerflow-runtime",
                },
            },
        }
    )


class TestThreadDataMiddleware:
    def test_before_agent_returns_paths_when_thread_id_present_in_context(self, tmp_path):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)

        result = middleware.before_agent(state={}, runtime=Runtime(context={"thread_id": "thread-123"}))

        assert result is not None
        assert _as_posix(result["thread_data"]["workspace_path"]).endswith("threads/thread-123/user-data/workspace")
        assert _as_posix(result["thread_data"]["uploads_path"]).endswith("threads/thread-123/user-data/uploads")
        assert _as_posix(result["thread_data"]["outputs_path"]).endswith("threads/thread-123/user-data/outputs")

    def test_before_agent_uses_thread_id_from_configurable_when_context_is_none(self, tmp_path, monkeypatch):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        runtime = Runtime(context=None)
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_config",
            lambda: {"configurable": {"thread_id": "thread-from-config"}},
        )

        result = middleware.before_agent(state={}, runtime=runtime)

        assert result is not None
        assert _as_posix(result["thread_data"]["workspace_path"]).endswith("threads/thread-from-config/user-data/workspace")
        assert runtime.context is None

    def test_before_agent_uses_thread_id_from_configurable_when_context_missing_thread_id(self, tmp_path, monkeypatch):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        runtime = Runtime(context={})
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_config",
            lambda: {"configurable": {"thread_id": "thread-from-config"}},
        )

        result = middleware.before_agent(state={}, runtime=runtime)

        assert result is not None
        assert _as_posix(result["thread_data"]["uploads_path"]).endswith("threads/thread-from-config/user-data/uploads")
        assert runtime.context == {}

    def test_before_agent_raises_clear_error_when_thread_id_missing_everywhere(self, tmp_path, monkeypatch):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_config",
            lambda: {"configurable": {}},
        )

        with pytest.raises(ValueError, match="Thread ID is required in runtime context or config.configurable"):
            middleware.before_agent(state={}, runtime=Runtime(context=None))

    def test_object_runtime_returns_virtual_paths_without_creating_thread_dirs(self, tmp_path, monkeypatch):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=False)
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_app_config",
            lambda: _object_runtime_config(),
        )
        monkeypatch.setattr(
            middleware._paths,
            "ensure_thread_dirs",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("object mode must not create thread dirs")),
        )

        result = middleware.before_agent(state={}, runtime=Runtime(context={"thread_id": "thread-object"}))

        assert result is not None
        assert result["thread_data"] == {
            "workspace_path": "/mnt/user-data/workspace",
            "uploads_path": "/mnt/user-data/uploads",
            "outputs_path": "/mnt/user-data/outputs",
        }
