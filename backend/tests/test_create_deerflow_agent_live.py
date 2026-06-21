"""Live integration tests for create_deerflow_agent.

Verifies the factory produces a working LangGraph agent that can actually
process messages end-to-end with a real LLM.

Tests marked ``requires_llm`` are skipped when the active file/DB model config
is not ready for the model-backed live gate.
"""

import uuid

import pytest
from langchain_core.tools import tool
from support.live_gate_readiness import mark_requires_llm

pytestmark = [pytest.mark.live, pytest.mark.requires_llm]

requires_llm = mark_requires_llm


def _make_model():
    """Create a real chat model from the active file/DB AppConfig."""
    from support.live_gate_readiness import load_active_app_config_for_requires_llm

    from deerflow.models import create_chat_model

    return create_chat_model(
        thinking_enabled=False,
        app_config=load_active_app_config_for_requires_llm(),
        attach_tracing=False,
    )


# ---------------------------------------------------------------------------
# 1. Minimal creation — model only, no features
# ---------------------------------------------------------------------------
@requires_llm
def test_minimal_agent_responds():
    """create_deerflow_agent(model) produces a graph that returns a response."""
    from deerflow.agents.factory import create_deerflow_agent

    model = _make_model()
    graph = create_deerflow_agent(model, features=None, middleware=[])

    result = graph.invoke(
        {"messages": [("user", "Say exactly: pong")]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )

    messages = result.get("messages", [])
    assert len(messages) >= 2
    last_msg = messages[-1]
    assert hasattr(last_msg, "content")
    assert len(last_msg.content) > 0


# ---------------------------------------------------------------------------
# 2. With custom tool — verifies tool injection and execution
# ---------------------------------------------------------------------------
@requires_llm
def test_agent_with_custom_tool():
    """Agent can invoke a user-provided tool and return the result."""
    from deerflow.agents.factory import create_deerflow_agent

    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    model = _make_model()
    graph = create_deerflow_agent(model, tools=[add], middleware=[])

    result = graph.invoke(
        {"messages": [("user", "Use the add tool to compute 3 + 7. Return only the result.")]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )

    messages = result.get("messages", [])
    # Should have: user msg, AI tool_call, tool result, AI final
    assert len(messages) >= 3
    last_content = messages[-1].content
    assert "10" in last_content


# ---------------------------------------------------------------------------
# 3. RuntimeFeatures mode — middleware chain runs without errors
# ---------------------------------------------------------------------------
@requires_llm
def test_features_mode_middleware_chain():
    """RuntimeFeatures assembles a working middleware chain that executes."""
    from deerflow.agents.factory import create_deerflow_agent
    from deerflow.agents.features import RuntimeFeatures

    model = _make_model()
    feat = RuntimeFeatures(sandbox=False, auto_title=False, memory=False)
    graph = create_deerflow_agent(model, features=feat)

    result = graph.invoke(
        {"messages": [("user", "What is 2+2?")]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )

    messages = result.get("messages", [])
    assert len(messages) >= 2
    last_content = messages[-1].content
    assert len(last_content) > 0
