from types import SimpleNamespace

from reasoning.evidence import build_evidence


def test_temporal_context_is_preserved():
    result = SimpleNamespace(
        query="What class do I have now?",
        intent=SimpleNamespace(name="schedule_query"),
        answer_type="schedule_context",
        data={
            "time_context": {
                "personal_context": {
                    "current_class": {
                        "value": "DBMS",
                        "context_type": "schedule",
                        "updated_at": "2026-08-24T10:00:00+00:00",
                        "valid_from": "2026-08-24T09:00:00+00:00",
                        "valid_until": "2026-08-24T11:00:00+00:00",
                    }
                }
            },
            "documents": [],
        },
    )

    evidence = build_evidence(result)

    assert len(evidence["temporal_context"]) == 1

    item = evidence["temporal_context"][0]

    assert item["key"] == "current_class"
    assert item["value"] == "DBMS"
    assert item["valid_from"] == "2026-08-24T09:00:00+00:00"
    assert item["valid_until"] == "2026-08-24T11:00:00+00:00"


def test_temporal_context_empty_when_missing():
    result = SimpleNamespace(
        query="What do I have now?",
        intent=SimpleNamespace(name="schedule_query"),
        answer_type="schedule_context",
        data={
            "time_context": {
                "personal_context": {}
            },
            "documents": [],
        },
    )

    evidence = build_evidence(result)

    assert evidence["temporal_context"] == []