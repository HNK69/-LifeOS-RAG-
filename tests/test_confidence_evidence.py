from types import SimpleNamespace

from reasoning.evidence import build_evidence


def test_empty_evidence_has_zero_confidence():
    result = SimpleNamespace(
        query="unknown",
        intent=SimpleNamespace(name="unknown"),
        answer_type="unknown",
        data=None,
    )

    evidence = build_evidence(result)

    assert evidence["confidence"] == 0.0


def test_document_evidence_has_confidence():
    result = SimpleNamespace(
        query="What is LifeOS?",
        intent=SimpleNamespace(name="document_search"),
        answer_type="documents",
        data=[
            {
                "source": "notes.txt",
                "file_path": "/data/notes.txt",
                "document": "LifeOS is a personal AI system.",
            }
        ],
    )

    evidence = build_evidence(result)

    assert evidence["confidence"] == 0.8


def test_relationship_evidence_has_high_confidence():
    result = SimpleNamespace(
        query="Who does Alice know?",
        intent=SimpleNamespace(name="relationship_search"),
        answer_type="relationships",
        data=[
            {
                "relationship_type": "knows",
                "source": {"canonical_name": "Alice"},
                "target": {"canonical_name": "Bob"},
            }
        ],
    )

    evidence = build_evidence(result)

    assert evidence["confidence"] == 0.9