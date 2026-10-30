from unittest.mock import patch

from query.router import QueryResult


def test_handle_query_uses_agent_for_supported_document_search():
    from app import main

    agent_result = QueryResult(
        query="college notes",
        intent="document_search",
        answer_type="documents",
        data=[
            {
                "document": "Operating Systems notes"
            }
        ],
    )

    with patch(
        "app.main._try_agent_query",
        return_value=agent_result,
    ), patch(
        "app.main._format_document_result",
        return_value="Agent answer",
    ) as format_document:

        response = main.handle_query("college notes")

    assert response == "Agent answer"
    format_document.assert_called_once_with(agent_result)