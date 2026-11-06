import json
from unittest.mock import patch

from app.agent.adapter import agent_state_to_query_result
from app.agent.decision import AgentDecision, AgentPlanner
from app.agent.executor import AgentExecutor
from app.agent.runner import AgentRunner
from app.agent.tools import (
    RelationshipSearchInput,
    RelationshipSearchTool,
    create_default_registry,
)
from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
)
from query.router import QueryResult


def test_relationship_search_input_validation():
    valid = RelationshipSearchInput(entity_name="Alice")
    assert valid.entity_name == "Alice"
    assert valid.direction == "both"

    # Using query field fallback
    from_query = RelationshipSearchInput(query="Bob")
    assert from_query.entity_name == "Bob"


def test_relationship_search_tool_execution():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")
    create_relationship(alice["id"], bob["id"], "mentors", confidence=0.92)

    tool = RelationshipSearchTool()
    tool_result = tool.execute(
        RelationshipSearchInput(
            entity_name="Alice",
            direction="outgoing",
        )
    )

    assert tool_result.success is True
    query_result = tool_result.data
    assert isinstance(query_result, QueryResult)
    assert query_result.answer_type == "relationships"
    assert len(query_result.data) == 1
    assert query_result.data[0]["relationship_type"] == "mentors"
    assert query_result.data[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_agent_planner_relationship_search():
    registry = create_default_registry()
    planner = AgentPlanner(registry)

    mock_plan = json.dumps({
        "intent": "relationship_search",
        "confidence": 0.9,
        "arguments": {
            "entity_name": "Alice",
            "direction": "both",
        },
    })

    with patch("intelligence.planner.generate_response", return_value=mock_plan):
        decision = planner.decide("Who does Alice know?")

    assert decision.action == "tool"
    assert decision.tool_name == "relationship_search"
    assert decision.arguments["entity_name"] == "Alice"


def test_agent_runner_relationship_search_end_to_end():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")
    create_relationship(alice["id"], bob["id"], "colleague", confidence=0.9)

    registry = create_default_registry()
    planner = AgentPlanner(registry)
    executor = AgentExecutor(registry)
    runner = AgentRunner(planner=planner, executor=executor, max_steps=3)

    mock_plan = json.dumps({
        "intent": "relationship_search",
        "confidence": 0.95,
        "arguments": {
            "entity_name": "Alice",
            "direction": "both",
        },
    })

    with patch("intelligence.planner.generate_response", return_value=mock_plan):
        state = runner.run("Who is Alice connected with?")

    assert state.status == "completed"
    assert len(state.tool_calls) == 1
    assert state.tool_calls[0].tool_name == "relationship_search"
    assert state.tool_calls[0].error is None

    result = agent_state_to_query_result(state)
    assert isinstance(result, QueryResult)
    assert result.answer_type == "relationships"
    assert len(result.data) == 1
    assert result.data[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()
