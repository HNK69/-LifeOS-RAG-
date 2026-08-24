from types import SimpleNamespace

from reasoning.evidence import build_evidence


def test_document_evidence():
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

    assert evidence["sources"][0]["source"] == "notes.txt"
    assert "LifeOS" in evidence["sources"][0]["content"]


def test_relationship_evidence():
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

    assert len(evidence["relationships"]) == 1


def test_schedule_context_and_documents():
    result = SimpleNamespace(
        query="What class do I have?",
        intent=SimpleNamespace(name="schedule_query"),
        answer_type="schedule_context",
        data={
            "time_context": {
                "personal_context": {
                    "context": {
                        "current_class": {
                            "value": "DBMS"
                        }
                    }
                }
            },
            "documents": [
                {
                    "source": "schedule.pdf",
                    "file_path": "/data/schedule.pdf",
                    "document": "DBMS at 10 AM",
                }
            ],
        },
    )

    evidence = build_evidence(result)

    assert evidence["personal_context"]["context"]["current_class"]["value"] == "DBMS"
    assert evidence["sources"][0]["source"] == "schedule.pdf"


def test_empty_result():
    result = SimpleNamespace(
        query="unknown",
        intent=SimpleNamespace(name="unknown"),
        answer_type="unknown",
        data=None,
    )

    evidence = build_evidence(result)

    assert evidence["sources"] == []
    assert evidence["structured_data"] == []
    assert evidence["relationships"] == []
    assert evidence["personal_context"] == {}
    assert evidence["multimodal"] == []