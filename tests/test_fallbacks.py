"""
test_fallbacks.py

Deterministic fallback behavior tests.
Replaces previous unmocked script that executed live pipeline queries at import time.
"""

import sys
import json
import pytest

from app import main
from app.intelligence.models import IntentPlan


FALLBACK_QUERIES = [
    "Find a file called xyz_nonexistent_123.pdf",
    "Where are my nonexistent Java lab programs?",
    "What does my nonexistent document say about quantum networking?",
    "Find the nonexistent customer churn dataset xyz_123.csv",
]


@pytest.mark.parametrize("query", FALLBACK_QUERIES)
def test_handle_query_handles_nonexistent_data_gracefully(query, monkeypatch):
    """
    Ensure queries for nonexistent documents/files return graceful fallback
    responses without unhandled exceptions or live LLM network calls.
    """
    main.clear_session()

    fallback_plan = IntentPlan(
        intent="document_search",
        confidence=0.5,
        arguments={},
    )

    # Mock plan_query directly in main and planner modules
    monkeypatch.setattr(main, "plan_query", lambda q: fallback_plan)
    for mod in ["intelligence.planner", "app.intelligence.planner"]:
        if mod in sys.modules:
            monkeypatch.setattr(sys.modules[mod], "plan_query", lambda q: fallback_plan)

    # Mock LLM generation in all generator and engine references
    fallback_text = "I could not find any relevant information for your query."
    for mod in ["llm.generator", "app.llm.generator", "reasoning.engine", "app.reasoning.engine"]:
        if mod in sys.modules:
            monkeypatch.setattr(sys.modules[mod], "generate_response", lambda prompt: fallback_text)

    result = main.handle_query(query)
    assert result is not None
    assert isinstance(result, str) or hasattr(result, "content") or hasattr(result, "data")
