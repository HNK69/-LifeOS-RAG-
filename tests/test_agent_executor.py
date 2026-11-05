from unittest.mock import patch
from pydantic import BaseModel

from app.agent.executor import AgentExecutor
from app.agent.state import AgentState
from app.agent.tools import (
    AgentTool,
    CurrentTimeInput,
    CurrentTimeTool,
    DocumentSearchTool,
    ToolRegistry,
    ToolResult,
)
from query.router import QueryIntent, QueryResult


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
    assert result.evidence is not None


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
    assert result.tool_calls[0].error == "Unknown tool: unknown_tool"


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


def test_executor_rejects_invalid_tool_arguments():
    registry = ToolRegistry(tools=[DocumentSearchTool()])
    executor = AgentExecutor(registry)

    state = AgentState(query="search")

    # DocumentSearchInput requires query of min_length=1
    result = executor.execute_tool(
        state=state,
        tool_name="document_search",
        arguments={"query": ""},
    )

    assert result.status == "failed"
    assert "validation error" in result.error.lower()
    assert result.tool_calls[0].error is not None


def test_executor_handles_tool_execution_failure():
    class FailingInput(BaseModel):
        target: str

    class FailingTool(AgentTool):
        name = "failing_tool"
        description = "Tool that returns failure"
        input_schema = FailingInput

        def execute(self, input_data: FailingInput) -> ToolResult:
            return ToolResult(
                success=False,
                error="Underlying service unavailable.",
            )

    registry = ToolRegistry(tools=[FailingTool()])
    executor = AgentExecutor(registry)

    state = AgentState(query="fail me")

    result = executor.execute_tool(
        state=state,
        tool_name="failing_tool",
        arguments={"target": "service"},
    )

    assert result.status == "failed"
    assert result.error == "Underlying service unavailable."
    assert result.tool_calls[0].error == "Underlying service unavailable."


def test_executor_preserves_structured_evidence_with_query_result():
    registry = ToolRegistry(tools=[DocumentSearchTool()])
    executor = AgentExecutor(registry)

    state = AgentState(query="operating systems")

    query_res = QueryResult(
        query="operating systems",
        intent=QueryIntent("document_search", 1.0, {}),
        answer_type="documents",
        data=[
            {"document": "OS Chapter 1", "source": "os.pdf"},
            {"document": "OS Chapter 2", "source": "os2.pdf"},
        ],
    )

    with patch("app.agent.tools._document_search", return_value=query_res):
        executor.execute_tool(
            state=state,
            tool_name="document_search",
            arguments={"query": "operating systems"},
        )

    assert state.evidence is not None
    assert state.evidence["query"] == "operating systems"
    assert len(state.evidence["tool_calls"]) == 1
    assert state.evidence["tool_calls"][0]["answer_type"] == "documents"
    assert "os.pdf" in state.evidence["sources"]
    assert "os2.pdf" in state.evidence["sources"]
