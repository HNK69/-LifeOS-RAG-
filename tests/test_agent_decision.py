from unittest.mock import patch
import pytest

from app.agent.decision import AgentDecision, AgentPlanner
from app.agent.state import AgentState, ToolCall
from app.agent.tools import (
    CurrentTimeTool,
    DocumentSearchTool,
    FileDiscoveryTool,
    ToolRegistry,
)
from intelligence.models import IntentPlan


def create_planner():
    registry = ToolRegistry(
        tools=[
            CurrentTimeTool(),
            DocumentSearchTool(),
            FileDiscoveryTool(),
        ]
    )
    return AgentPlanner(registry)


def test_agent_decision():
    decision = AgentDecision(
        tool_name="document_search",
        arguments={"query": "college notes"},
    )

    assert decision.action == "tool"
    assert decision.tool_name == "document_search"
    assert decision.arguments == {
        "query": "college notes",
    }
    assert decision.has_more_steps is False
    assert decision.final_answer is None
    assert decision.error is None


def test_agent_decision_constructors():
    tool_dec = AgentDecision.call_tool(
        "file_discovery",
        {"query": "notes"},
        has_more_steps=True,
    )
    assert tool_dec.action == "tool"
    assert tool_dec.tool_name == "file_discovery"
    assert tool_dec.arguments == {"query": "notes"}
    assert tool_dec.has_more_steps is True

    finish_dec = AgentDecision.finish("Task completed successfully.")
    assert finish_dec.action == "finish"
    assert finish_dec.final_answer == "Task completed successfully."
    assert finish_dec.tool_name is None

    fail_dec = AgentDecision.fail("Capability missing.")
    assert fail_dec.action == "fail"
    assert fail_dec.error == "Capability missing."


def test_agent_planner_uses_existing_llm_planner():
    planner = create_planner()

    mocked_plan = IntentPlan(
        intent="document_search",
        confidence=0.95,
    )

    with patch(
        "app.agent.decision.plan_query",
        return_value=mocked_plan,
    ) as mock_plan:
        decision = planner.decide(" college notes ")

    mock_plan.assert_called_once_with("college notes")

    assert decision.action == "tool"
    assert decision.tool_name == "document_search"
    assert decision.arguments == {
        "query": "college notes",
    }


def test_agent_planner_rejects_empty_query():
    planner = create_planner()

    with pytest.raises(
        ValueError,
        match="Query must not be empty",
    ):
        planner.decide("   ")


def test_agent_planner_rejects_unregistered_intent():
    planner = create_planner()

    mocked_plan = IntentPlan(
        intent="unknown",
        confidence=0.95,
    )

    with patch(
        "app.agent.decision.plan_query",
        return_value=mocked_plan,
    ):
        with pytest.raises(
            ValueError,
            match="No agent tool registered for intent: unknown",
        ):
            planner.decide("something unsupported")


def test_agent_planner_maps_file_discovery_to_tool():
    planner = create_planner()

    mocked_plan = IntentPlan(
        intent="file_discovery",
        confidence=0.95,
    )

    with patch(
        "app.agent.decision.plan_query",
        return_value=mocked_plan,
    ):
        decision = planner.decide(
            "find my operating systems notes"
        )

    assert decision.action == "tool"
    assert decision.tool_name == "file_discovery"

    # IntentPlan supplies default StructuredArguments fields.
    assert decision.arguments["filters"] == {}
    assert decision.arguments["limit"] == 10
    assert decision.arguments["descending"] is False


def test_agent_planner_maps_current_time_to_tool():
    planner = create_planner()

    mocked_plan = IntentPlan(
        intent="current_time",
        confidence=0.95,
    )

    with patch(
        "app.agent.decision.plan_query",
        return_value=mocked_plan,
    ):
        decision = planner.decide("what time is it?")

    assert decision.action == "tool"
    assert decision.tool_name == "current_time"


def test_agent_planner_handles_subsequent_step_when_state_completed():
    planner = create_planner()

    state = AgentState(
        query="what time is it?",
        status="running",
        step_count=1,
        tool_calls=[
            ToolCall(
                tool_name="current_time",
                arguments={"query": "what time is it?"},
                result={"time": "12:00:00"},
            )
        ],
    )

    decision = planner.decide("what time is it?", state=state)

    assert decision.action == "finish"


def test_agent_planner_handles_subsequent_step_when_previous_call_failed():
    planner = create_planner()

    state = AgentState(
        query="search document",
        status="running",
        step_count=1,
        tool_calls=[
            ToolCall(
                tool_name="document_search",
                arguments={"query": "doc"},
                error="Database index unavailable",
            )
        ],
    )

    decision = planner.decide("search document", state=state)

    assert decision.action == "fail"
    assert "Previous step failed" in decision.error


def test_agent_planner_propagates_underlying_planner_failure():
    planner = create_planner()

    with patch(
        "app.agent.decision.plan_query",
        side_effect=RuntimeError("LLM planner timeout"),
    ):
        with pytest.raises(RuntimeError, match="LLM planner timeout"):
            planner.decide("my query")
