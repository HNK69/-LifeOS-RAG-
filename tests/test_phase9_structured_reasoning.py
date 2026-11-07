"""
test_phase9_structured_reasoning.py

Tests verifying structured reasoning output, backward compatibility with strings,
and the strict deterministic separation of grounded FACTS vs INFERENCES.
"""
from app.reasoning.engine import reason, reason_structured
from app.reasoning.models import ReasoningResult


def test_structured_reasoning_string_backward_compatibility():
    """ReasoningResult behaves as a true str for equality, printing, and string methods."""
    res = ReasoningResult(
        answer="The project deadline is Friday.",
        confidence=0.88,
        facts=["Deadline is Friday."],
        inferences=["Project is in final phase."],
        evidence_used=[{"type": "document", "source": "plan.pdf"}],
        conflicts_detected=[],
    )

    # String behaviors
    assert isinstance(res, str)
    assert res == "The project deadline is Friday."
    assert "deadline" in res
    assert f"Answer: {res}" == "Answer: The project deadline is Friday."
    assert res.startswith("The project")

    # Structured properties
    assert res.confidence == 0.88
    assert res.facts == ["Deadline is Friday."]
    assert res.inferences == ["Project is in final phase."]
    assert len(res.evidence_used) == 1
    assert res.conflicts_detected == []

    # Dictionary export
    d = res.to_dict()
    assert d["answer"] == "The project deadline is Friday."
    assert d["confidence"] == 0.88
    assert d["facts"] == ["Deadline is Friday."]


def test_fact_vs_inference_separation(monkeypatch):
    """
    Direct assertions supported by evidence remain FACTS.
    Extrapolations or ungrounded claims are classified as INFERENCES.
    """
    evidence = {
        "sources": [
            {
                "source": "contract.pdf",
                "content": "Alice Chen started employment on January 15, 2024.",
            }
        ],
        "relationships": [],
        "temporal_context": [],
        "confidence": 0.85,
    }

    mock_llm_response = """
ANSWER: Alice Chen has been with the company since early 2024.
FACTS:
- Alice Chen started employment on January 15, 2024.
- Alice Chen is a senior executive.
INFERENCES:
- Alice Chen likely has completed her onboarding.
"""
    monkeypatch.setattr("app.reasoning.engine.generate_response", lambda prompt: mock_llm_response)

    result = reason("When did Alice Chen join and what is her status?", evidence)

    assert isinstance(result, ReasoningResult)
    # The grounded fact is confirmed
    assert any("January 15, 2024" in f for f in result.facts)
    # The ungrounded statement ("senior executive" not in evidence) is demoted to inference
    assert any("senior executive" in inf for inf in result.inferences)
    # The explicit inference is preserved
    assert any("onboarding" in inf for inf in result.inferences)


def test_evidence_used_originates_from_deterministic_package():
    """Evidence references in ReasoningResult are assembled by Python, preventing hallucinated citations."""
    evidence = {
        "sources": [
            {
                "source": "report_q3.pdf",
                "file_path": "/data/report_q3.pdf",
                "content": "Q3 revenue grew by 15 percent.",
            }
        ],
        "relationships": [
            {
                "source_name": "Finance Department",
                "target_name": "LifeOS",
                "relationship_type": "funds",
                "confidence": 0.95,
            }
        ],
        "temporal_context": [
            {
                "key": "fiscal_year",
                "value": "2026",
            }
        ],
        "conflicts": [],
        "confidence": 0.92,
    }

    def fake_llm(prompt):
        # LLM tries to cite a fake document
        return "Q3 revenue grew by 15 percent according to non_existent_file.pdf."

    from unittest.mock import patch
    with patch("app.reasoning.engine.generate_response", side_effect=fake_llm):
        res = reason_structured("What was Q3 revenue growth?", evidence)

    # Evidence used MUST contain only the real evidence from Python
    sources = [e.get("source") for e in res.evidence_used if e.get("type") == "document"]
    assert "report_q3.pdf" in sources
    assert "non_existent_file.pdf" not in sources
    assert len(res.evidence_used) == 3  # doc + relationship + context
