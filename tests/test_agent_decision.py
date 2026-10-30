from unittest.mock import patch
import pytest
from app.agent.decision import AgentDecision, AgentPlanner
from intelligence.models import IntentPlan
from app.agent.tools import (
    CurrentTimeTool,
    DocumentSearchTool,
    FileDiscoveryTool,
    ToolRegistry,
)

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

    assert decision.tool_name == "document_search"
    assert decision.arguments == {
        "query": "college notes",
    }


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

    assert decision.tool_name == "current_time"