from reasoning.engine import build_reasoning_prompt


def test_prompt_requires_multi_evidence_synthesis():
    evidence = {
        "sources": [
            {
                "source": "schedule.pdf",
                "content": "DBMS is scheduled at 10 AM.",
            }
        ],
        "structured_data": [
            {
                "dataset": "classes.csv",
                "result": {"class": "DBMS", "time": "10 AM"},
            }
        ],
        "personal_context": {
            "current_class": {
                "value": "DBMS",
            }
        },
        "relationships": [],
        "multimodal": [],
        "temporal_context": [],
        "conflicts": [],
        "confidence": 0.9,
    }

    prompt = build_reasoning_prompt(
        "What class do I have?",
        evidence,
    )

    assert "Synthesize information across all evidence types" in prompt
    assert "Cross-check personal context against documents" in prompt
    assert "confidence score" in prompt


def test_prompt_contains_all_evidence_types():
    evidence = {
        "sources": ["document evidence"],
        "structured_data": ["structured evidence"],
        "relationships": ["relationship evidence"],
        "personal_context": {"location": "college"},
        "multimodal": ["image evidence"],
        "temporal_context": ["temporal evidence"],
        "conflicts": [],
        "confidence": 0.8,
    }

    prompt = build_reasoning_prompt(
        "What is happening?",
        evidence,
    )

    for value in (
        "document evidence",
        "structured evidence",
        "relationship evidence",
        "college",
        "image evidence",
        "temporal evidence",
    ):
        assert value in prompt