from context.context_store import (
    clear_context,
    compose_context,
)

from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
)


def test_compose_context_includes_relationships():
    clear_context()
    clear_entity_store()

    alice = create_entity(
        "person",
        "Alice",
    )

    bob = create_entity(
        "person",
        "Bob",
    )

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.9,
    )

    result = compose_context(
        "What does Alice know?"
    )

    assert result["relationship_count"] == 1

    relationship = result["relationships"][0]

    assert relationship["relationship_type"] == "knows"
    assert relationship["source"]["canonical_name"] == "Alice"
    assert relationship["target"]["canonical_name"] == "Bob"

    clear_context()
    clear_entity_store()


def test_compose_context_without_relationships():
    clear_context()
    clear_entity_store()

    result = compose_context(
        "What is my study routine?"
    )

    assert result["relationships"] == []
    assert result["relationship_count"] == 0

    clear_context()
    clear_entity_store()


def test_relationship_context_does_not_invent_relationships():
    clear_context()
    clear_entity_store()

    create_entity(
        "person",
        "Alice",
    )

    result = compose_context(
        "Alice works with Bob."
    )

    assert result["relationships"] == []
    assert result["relationship_count"] == 0

    clear_context()
    clear_entity_store()


def test_relationships_survive_context_to_evidence_pipeline(monkeypatch):
    from query.router import QueryResult, QueryIntent
    from reasoning.evidence import build_evidence
    from reasoning.engine import reason

    clear_context()
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")
    create_relationship(alice["id"], bob["id"], "advisor_of", confidence=0.95)

    composed = compose_context("What is my schedule with Alice?")
    assert composed["relationship_count"] == 1

    query_result = QueryResult(
        query="What is my schedule with Alice?",
        intent=QueryIntent("schedule_query", 1.0, {}),
        answer_type="schedule_context",
        data={
            "documents": [{"document": "Office hours on Friday", "source": "schedule.txt"}],
            "time_context": {
                "personal_context": composed,
            },
        },
    )

    evidence = build_evidence(query_result)

    # Verify relationships survived into evidence
    assert len(evidence["relationships"]) == 1
    rel = evidence["relationships"][0]
    assert rel["relationship_type"] == "advisor_of"
    assert rel["source"]["canonical_name"] == "Alice"
    assert rel["target"]["canonical_name"] == "Bob"
    assert evidence["confidence"] > 0.8

    # Verify reasoning prompt includes the relationship evidence
    captured_prompt = []
    monkeypatch.setattr(
        "reasoning.engine.generate_response",
        lambda prompt: captured_prompt.append(prompt) or "Alice is Bob's advisor.",
    )

    answer = reason(query_result.query, query_result)
    assert answer == "Alice is Bob's advisor."
    assert len(captured_prompt) == 1
    assert "advisor_of" in captured_prompt[0]

    clear_context()
    clear_entity_store()