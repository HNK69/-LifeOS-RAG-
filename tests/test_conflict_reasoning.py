from types import SimpleNamespace

from reasoning.evidence import build_evidence


def test_detects_conflicting_temporal_context():
    result = SimpleNamespace(
        query="What class do I have?",
        intent=SimpleNamespace(name="schedule_query"),
        answer_type="schedule_context",
        data={
            "time_context": {
                "personal_context": {
                    "current_class": {
                        "value": "DBMS",
                        "context_type": "schedule",
                        "updated_at": "2026-08-25T10:00:00+00:00",
                    },
                    "current_class_alt": {
                        "value": "OS",
                        "context_type": "schedule",
                        "updated_at": "2026-08-25T10:05:00+00:00",
                    },
                }
            },
            "documents": [],
        },
    )

    evidence = build_evidence(result)

    assert "conflicts" in evidence