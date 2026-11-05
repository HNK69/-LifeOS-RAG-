import pytest

from app.agent.adapter import agent_state_to_query_result
from app.agent.state import AgentState, ToolCall
from query.router import QueryIntent, QueryResult


def test_agent_state_converts_to_query_result():
    expected = QueryResult(
        query="college notes",
        intent=QueryIntent(
            "document_search",
            1.0,
            {"method": "semantic_chunks"},
        ),
        answer_type="documents",
        data=[
            {"document": "Operating Systems notes"}
        ],
    )

    state = AgentState(
        query="college notes",
        status="completed",
        step_count=1,
        tool_calls=[
            ToolCall(
                tool_name="document_search",
                arguments={"query": "college notes"},
                result=expected,
            )
        ],
    )

    result = agent_state_to_query_result(state)

    assert result == expected
    assert result.intent.name == "document_search"
    assert result.answer_type == "documents"
    assert result.data == [
        {"document": "Operating Systems notes"}
    ]


def test_failed_agent_state_cannot_convert():
    state = AgentState(
        query="college notes",
        status="failed",
        error="Search failed.",
    )

    with pytest.raises(
        ValueError,
        match="Only completed agent executions can be converted",
    ):
        agent_state_to_query_result(state)


def test_agent_state_without_tool_calls_cannot_convert():
    state = AgentState(
        query="college notes",
        status="completed",
    )

    with pytest.raises(
        ValueError,
        match="no tool calls",
    ):
        agent_state_to_query_result(state)


def test_file_discovery_result_preserves_answer_type():
    expected = QueryResult(
        query="python files",
        intent=QueryIntent("file_discovery", 0.9, {}),
        answer_type="files",
        data=[],
    )

    state = AgentState(
        query="python files",
        status="completed",
        tool_calls=[
            ToolCall(
                tool_name="file_discovery",
                arguments={"query": "python files"},
                result=expected,
            )
        ],
    )

    result = agent_state_to_query_result(state)

    assert result == expected
    assert result.answer_type == "files"


def test_current_time_result_preserves_answer_type():
    expected = QueryResult(
        query="what time is it?",
        intent=QueryIntent("current_time", 1.0, {}),
        answer_type="time",
        data={"time": "12:00:00"},
    )

    state = AgentState(
        query="what time is it?",
        status="completed",
        tool_calls=[
            ToolCall(
                tool_name="current_time",
                arguments={"query": "what time is it?"},
                result=expected,
            )
        ],
    )

    result = agent_state_to_query_result(state)

    assert result == expected
    assert result.answer_type == "time"


def test_adapter_rejects_non_query_result_data():
    state = AgentState(
        query="query",
        status="completed",
        tool_calls=[
            ToolCall(
                tool_name="echo_tool",
                arguments={"message": "hello"},
                result={"echo": "hello"},
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match="Agent tool result is not a QueryResult",
    ):
        agent_state_to_query_result(state)


def test_adapter_rejects_tool_call_error_in_completed_state():
    state = AgentState(
        query="query",
        status="completed",
        tool_calls=[
            ToolCall(
                tool_name="failing_tool",
                arguments={},
                result=None,
                error="Fatal execution failure",
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match="Fatal execution failure",
    ):
        agent_state_to_query_result(state)
