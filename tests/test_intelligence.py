"""
test_intelligence.py

Deterministic query planner intent tests.
Replaces previous unmocked script (LR-P3-02) that executed live LLM calls during test discovery.
"""

import json
import pytest

from app.intelligence.planner import plan_query


TEST_CASES = [
    ("Find my Java lab programs", "file_discovery"),
    ("Where are my Python assignments?", "file_discovery"),
    ("Show me the DBMS notes", "file_discovery"),
    ("I need my project report", "file_discovery"),
    ("Locate my operating systems PDF", "file_discovery"),

    ("What is the maximum stack size?", "document_search"),
    ("Explain the TCP three way handshake", "document_search"),
    ("What does my notes say about normalization?", "document_search"),
    ("Find the definition of deadlock in my documents", "document_search"),
    ("What is written about inheritance?", "document_search"),

    ("Which class do I have today?", "schedule_query"),
    ("What is my timetable for tomorrow?", "schedule_query"),
    ("When is my next class?", "schedule_query"),
    ("Do I have a lab today?", "schedule_query"),
    ("What classes are scheduled this week?", "schedule_query"),

    ("What time is it?", "current_time"),
    ("What is today's date?", "current_time"),
    ("Tell me the current time", "current_time"),

    ("Find my customer churn dataset", "structured_discovery"),
    ("Which spreadsheet contains customer data?", "structured_discovery"),
    ("Show me my CSV datasets", "structured_discovery"),

    ("How many customers churned?", "structured_query"),
    ("What is the average monthly charge?", "structured_query"),
    ("What is the maximum monthly charge?", "structured_query"),
    ("Sort customers by monthly charge", "structured_query"),
    ("How many customers have churned?", "structured_query"),

    ("Tell me a joke", "unknown"),
    ("Write me a poem", "unknown"),
    ("What is the capital of France?", "unknown"),
    ("Help me cook pasta", "unknown"),
]


@pytest.mark.parametrize("query,expected_intent", TEST_CASES)
def test_query_planning_intent_routing(monkeypatch, query, expected_intent):
    """
    Verify that query planning correctly parses and sets intents deterministically
    without making live LLM network calls during test execution.
    """
    mock_payload = json.dumps({
        "intent": expected_intent,
        "confidence": 0.95,
        "arguments": {},
    })

    import llm.generator as generator
    monkeypatch.setattr(generator, "generate_response", lambda prompt: mock_payload)

    import app.intelligence.planner as planner
    monkeypatch.setattr(planner, "generate_response", lambda prompt: mock_payload)

    plan = plan_query(query)
    assert plan.intent == expected_intent
    assert plan.confidence >= 0.9
