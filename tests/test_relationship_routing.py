import json
from unittest.mock import patch

from intelligence.models import IntentPlan, StructuredArguments
from intelligence.planner import plan_query
from intelligence.router import execute_plan, route_intelligent
from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
)


def test_plan_query_relationship_intent(monkeypatch):
    mock_response = json.dumps({
        "intent": "relationship_search",
        "confidence": 0.95,
        "arguments": {
            "entity_name": "Alice",
            "direction": "outgoing",
        },
    })

    monkeypatch.setattr(
        "intelligence.planner.generate_response",
        lambda _: mock_response,
    )

    plan = plan_query("Who does Alice work with?")
    assert plan.intent == "relationship_search"
    assert plan.confidence == 0.95
    assert plan.arguments.entity_name == "Alice"
    assert plan.arguments.direction == "outgoing"


def test_plan_query_relationship_rescue_when_unknown(monkeypatch):
    # LLM returns unknown
    mock_response = json.dumps({
        "intent": "unknown",
        "confidence": 0.0,
        "arguments": {},
    })

    monkeypatch.setattr(
        "intelligence.planner.generate_response",
        lambda _: mock_response,
    )

    plan = plan_query("Who does Alice know?")
    assert plan.intent == "relationship_search"
    assert plan.arguments.entity_name == "Alice"
    assert plan.arguments.direction == "both"


def test_execute_plan_relationship_search():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")
    create_relationship(alice["id"], bob["id"], "knows", confidence=0.9)

    plan = IntentPlan(
        intent="relationship_search",
        confidence=0.95,
        arguments=StructuredArguments(
            entity_name="Alice",
            direction="both",
        ),
    )

    result = execute_plan("Who does Alice know?", plan)

    assert result.answer_type == "relationships"
    assert result.intent == plan
    assert len(result.data) == 1
    assert result.data[0]["relationship_type"] == "knows"
    assert result.data[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_route_intelligent_relationship_end_to_end(monkeypatch):
    clear_entity_store()

    alice = create_entity("person", "Alice")
    carol = create_entity("person", "Carol")
    create_relationship(alice["id"], carol["id"], "collaborator", confidence=0.88)

    mock_response = json.dumps({
        "intent": "relationship_search",
        "confidence": 0.9,
        "arguments": {
            "entity_name": "Alice",
            "relationship_type": "collaborator",
            "direction": "outgoing",
        },
    })

    monkeypatch.setattr(
        "intelligence.planner.generate_response",
        lambda _: mock_response,
    )

    result = route_intelligent("Who is Alice's collaborator?")

    assert result.answer_type == "relationships"
    assert len(result.data) == 1
    assert result.data[0]["target"]["canonical_name"] == "Carol"
    assert result.data[0]["relationship_type"] == "collaborator"

    clear_entity_store()
