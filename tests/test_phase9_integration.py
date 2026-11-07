"""
test_phase9_integration.py

Integration tests covering end-to-end reasoning, nested context normalization,
document reasoning, and multi-source evidence assembly for Phase 9.
"""
from types import SimpleNamespace
from unittest.mock import patch

from app import main
from context.context_store import clear_context, set_context
from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
)
from reasoning.aggregator import aggregate_evidence
from reasoning.engine import reason, reason_structured
from reasoning.evidence import build_evidence
from reasoning.models import ReasoningResult
from query.router import QueryIntent, QueryResult


def setup_function():
    clear_context()
    clear_entity_store()


def teardown_function():
    clear_context()
    clear_entity_store()


def test_end_to_end_document_reasoning_via_main(monkeypatch):
    """Factual document answers pass through reasoning engine and return ReasoningResult."""
    doc_result = QueryResult(
        query="Explain distributed transactions in LifeOS",
        intent=QueryIntent("document_search", 1.0, {"method": "semantic_chunks"}),
        answer_type="documents",
        data=[
            {
                "document": "Distributed transactions in LifeOS use a two-phase commit protocol.",
                "source": "architecture.pdf",
                "file_path": "/data/architecture.pdf",
                "distance": 0.15,
                "rerank_score": 12.5,
            }
        ],
    )

    monkeypatch.setattr(
        "app.main._try_agent_query",
        lambda q: None,
    )
    monkeypatch.setattr(
        "app.main.route_intelligent",
        lambda q: doc_result,
    )
    monkeypatch.setattr(
        "llm.generator.generate_response",
        lambda prompt: "LifeOS uses a two-phase commit protocol for distributed transactions.",
    )
    monkeypatch.setattr(
        "reasoning.engine.generate_response",
        lambda prompt: "LifeOS uses a two-phase commit protocol for distributed transactions.",
    )

    response = main.handle_query("Explain distributed transactions in LifeOS")

    assert isinstance(response, ReasoningResult)
    assert "two-phase commit protocol" in str(response)
    assert response.confidence > 0.8
    assert len(response.evidence_used) == 1
    assert response.evidence_used[0]["source"] == "architecture.pdf"
    assert response.conflicts_detected == []


def test_nested_context_normalization_end_to_end():
    """Real nested context produced by compose_context correctly normalizes into temporal_context."""
    set_context(
        "current_class",
        "Operating Systems",
        context_type="schedule",
        valid_from="2020-01-01T00:00:00+00:00",
        valid_until="2099-01-01T00:00:00+00:00",
    )

    from context.context_store import compose_context
    composed = compose_context("Which class do I have now?")

    schedule_result = QueryResult(
        query="Which class do I have now?",
        intent=QueryIntent("schedule_query", 1.0, {}),
        answer_type="schedule_context",
        data={
            "time_context": {
                "personal_context": composed,
            },
            "documents": [
                {
                    "source": "syllabus.pdf",
                    "file_path": "/docs/syllabus.pdf",
                    "document": "Operating Systems is held in Room 302.",
                    "distance": 0.20,
                }
            ],
        },
    )

    evidence = build_evidence(schedule_result)

    assert len(evidence["temporal_context"]) >= 1
    os_context = next(
        (t for t in evidence["temporal_context"] if t["key"] == "current_class"),
        None,
    )
    assert os_context is not None
    assert os_context["value"] == "Operating Systems"
    assert len(evidence["sources"]) == 1
    assert evidence["sources"][0]["source"] == "syllabus.pdf"


def test_multi_source_evidence_reaches_reasoning_engine(monkeypatch):
    """Prove at least two distinct sources (docs + context + relationships) simultaneously reach reason()."""
    set_context(
        "current_project",
        "LifeOS Reasoning",
        context_type="goal",
    )

    alice = create_entity("person", "Alice Chen")
    bob = create_entity("person", "Bob Miller")
    create_relationship(alice["id"], bob["id"], "collaborates_with", confidence=0.92)

    # Use the clean aggregation layer to combine sources
    composite_result = aggregate_evidence(
        query="Alice Chen LifeOS Reasoning",
        sources=["context", "relationships"],
        entity_name="Alice Chen",
    )

    assert composite_result.answer_type == "multi_source"
    assert len(composite_result.data["relationships"]) >= 1

    evidence = build_evidence(composite_result)
    assert len(evidence["relationships"]) >= 1

    captured_prompt = {}

    def fake_generate(prompt):
        captured_prompt["text"] = prompt
        return "Alice Chen collaborates with Bob Miller on LifeOS Reasoning."

    monkeypatch.setattr("llm.generator.generate_response", fake_generate)
    monkeypatch.setattr("reasoning.engine.generate_response", fake_generate)

    result = reason("Alice Chen LifeOS Reasoning", composite_result)

    assert isinstance(result, ReasoningResult)
    assert "Alice Chen" in captured_prompt["text"]
    assert "collaborates_with" in captured_prompt["text"]
    assert result.confidence > 0.85
    assert len(result.evidence_used) >= 2


def test_empty_documents_returns_safe_fallback():
    """Empty document search returns safe fallback without failing."""
    empty_result = QueryResult(
        query="Quantum teleportation",
        intent=QueryIntent("document_search", 1.0, {}),
        answer_type="documents",
        data=[],
    )
    response = main._format_document_result(empty_result)
    assert response == "I couldn't find relevant information in your documents."
