from unittest.mock import patch

from app.agent.executor import AgentExecutor
from app.agent.state import AgentState
from app.agent.tools import (
    DocumentSearchTool,
    ToolRegistry,
    ToolResult,
)


def test_executor_runs_registered_tool():
    registry = ToolRegistry(
        tools=[DocumentSearchTool()]
    )

    executor = AgentExecutor(registry)
    state = AgentState(query="find my college notes")

    with patch(
        "app.agent.tools._document_search",
        return_value={"documents": ["college notes"]},
    ):
        result = executor.execute_tool(
            state=state,
            tool_name="document_search",
            arguments={"query": "college notes"},
        )

    assert result.status == "running"
    assert result.step_count == 1
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "document_search"
    assert result.tool_calls[0].result == {
        "documents": ["college notes"]
    }


def test_executor_rejects_unknown_tool():
    registry = ToolRegistry()
    executor = AgentExecutor(registry)

    state = AgentState(query="test")

    result = executor.execute_tool(
        state=state,
        tool_name="unknown_tool",
        arguments={},
    )

    assert result.status == "failed"
    assert result.error == "Unknown tool: unknown_tool"
    assert result.step_count == 1


def test_executor_enforces_step_limit():
    registry = ToolRegistry()
    executor = AgentExecutor(registry)

    state = AgentState(
        query="test",
        max_steps=0,
    )

    result = executor.execute_tool(
        state=state,
        tool_name="unknown_tool",
        arguments={},
    )

    assert result.status == "failed"
    assert result.error == "Maximum agent steps exceeded."
    assert result.step_count == 0

from unittest.mock import patch

from app.agent.executor import AgentExecutor
from app.agent.state import AgentState
from app.agent.tools import (
    CurrentTimeInput,
    CurrentTimeTool,
    ToolRegistry,
)


def test_executor_runs_current_time_tool():
    registry = ToolRegistry(
        tools=[CurrentTimeTool()]
    )

    executor = AgentExecutor(registry)

    state = AgentState(
        query="what time is it?"
    )

    with patch(
        "app.agent.tools._current_time",
        return_value={"time": "12:00:00"},
    ):
        result = executor.execute_tool(
            state=state,
            tool_name="current_time",
            arguments={"query": "what time is it?"},
        )

    assert result.status == "running"
    assert result.step_count == 1
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "current_time"
    assert result.tool_calls[0].result == {"time": "12:00:00"}