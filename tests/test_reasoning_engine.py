from reasoning.engine import (
    build_reasoning_prompt,
    reason,
)


def test_reasoning_prompt_contains_query_and_evidence():
    prompt = build_reasoning_prompt(
        "Who does Alice know?",
        {
            "relationships": [
                {
                    "source": "Alice",
                    "target": "Bob",
                    "type": "knows",
                }
            ]
        },
    )

    assert "Who does Alice know?" in prompt
    assert "Alice" in prompt
    assert "Bob" in prompt
    assert "knows" in prompt


def test_reason_returns_grounded_response(monkeypatch):
    monkeypatch.setattr(
        "reasoning.engine.generate_response",
        lambda prompt: "Alice knows Bob.",
    )

    result = reason(
        "Who does Alice know?",
        {
            "relationships": [
                {
                    "source": "Alice",
                    "target": "Bob",
                    "type": "knows",
                }
            ]
        },
    )

    assert result == "Alice knows Bob."


def test_reason_empty_evidence():
    result = reason(
        "Who does Alice know?",
        {},
    )

    assert result == "I don't have enough information to answer that."


def test_reason_empty_llm_response(monkeypatch):
    monkeypatch.setattr(
        "reasoning.engine.generate_response",
        lambda prompt: None,
    )

    result = reason(
        "Who does Alice know?",
        {"relationships": []},
    )

    assert result == "I don't have enough information to answer that."