from unittest.mock import patch

from app import main
from query.router import QueryIntent, QueryResult
from reasoning.models import ReasoningResult


def test_format_reasoning_result_passes_evidence_to_reasoning_engine():
    """BUG-02 Regression: Verify that _format_reasoning_result passes QueryResult evidence to reason()."""
    test_result = QueryResult(
        query="What did Alice say about project deadlines?",
        intent=QueryIntent(name="document_search", confidence=0.92, arguments={}),
        answer_type="documents",
        data=[
            {
                "document": "Alice: The project deadline is November 15th.",
                "source": "meeting_notes.txt",
                "file_path": "data/documents/meeting_notes.txt",
                "distance": 0.12,
                "rerank_score": 8.5,
                "chunk_id": 0,
            }
        ],
    )

    mock_reasoning_result = ReasoningResult(
        answer="Alice stated that the deadline is November 15th.",
        confidence=0.92,
        evidence_used=[
            {
                "type": "document",
                "source": "meeting_notes.txt",
                "file_path": "data/documents/meeting_notes.txt",
                "content": "Alice: The project deadline is November 15th.",
            }
        ],
        facts=["The project deadline is November 15th."],
        inferences=[],
        conflicts_detected=[],
    )

    with patch("app.main.reason", return_value=mock_reasoning_result) as mock_reason:
        formatted = main._format_reasoning_result(test_result)

        mock_reason.assert_called_once_with(
            test_result.query,
            test_result,
        )
        assert formatted.answer == "Alice stated that the deadline is November 15th."
        assert formatted.confidence == 0.92
        assert len(formatted.evidence_used) == 1
        assert formatted.facts == ["The project deadline is November 15th."]
