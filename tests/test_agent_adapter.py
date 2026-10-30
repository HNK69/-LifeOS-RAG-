import pytest

from app.agent.adapter import agent_state_to_query_result
from app.agent.state import AgentState, ToolCall


def test_agent_state_converts_to_query_result():
    state = AgentState(
        query="college notes",
        status="completed",
        step_count=1,
        tool_calls=[
            ToolCall(
                tool_name="document_search",
                arguments={"query": "college notes"},
                result=[
                    {
                        "document": "Operating Systems notes"
                    }
                ],
            )
        ],
    )

    result = agent_state_to_query_result(state)

    assert result.query == "college notes"
    assert result.intent == "document_search"
    assert result.answer_type == "documents"
    assert result.data == [
        {
            "document": "Operating Systems notes"
        }
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