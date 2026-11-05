import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agent.state import AgentState, ToolCall
from query.router import QueryIntent, QueryResult


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


def test_single_agent_definitions_in_main():
    main_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    func_names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]

    assert func_names.count("_create_agent_runner") == 1
    assert func_names.count("_try_agent_query") == 1


def test_try_agent_query_returns_query_result_not_agent_state():
    from app import main

    expected = QueryResult(
        query="college notes",
        intent=QueryIntent("document_search", 1.0, {"method": "semantic_chunks"}),
        answer_type="documents",
        data=[{"document": "Operating Systems notes"}],
    )
    completed_state = AgentState(
        query="college notes",
        status="completed",
        tool_calls=[
            ToolCall(
                tool_name="document_search",
                arguments={"query": "college notes"},
                result=expected,
            )
        ],
    )

    mock_runner = MagicMock()
    mock_runner.run.return_value = completed_state

    with patch("app.main._create_agent_runner", return_value=mock_runner):
        result = main._try_agent_query("college notes")

    assert isinstance(result, QueryResult)
    assert not isinstance(result, AgentState)
    assert result == expected


def test_try_agent_query_returns_none_on_failed_agent_state():
    from app import main

    failed_state = AgentState(
        query="college notes",
        status="failed",
        error="Execution failed.",
    )

    mock_runner = MagicMock()
    mock_runner.run.return_value = failed_state

    with patch("app.main._create_agent_runner", return_value=mock_runner):
        result = main._try_agent_query("college notes")

    assert result is None


def test_try_agent_query_returns_none_on_adapter_conversion_error():
    from app import main

    state_without_tools = AgentState(
        query="college notes",
        status="completed",
        tool_calls=[],
    )

    mock_runner = MagicMock()
    mock_runner.run.return_value = state_without_tools

    with patch("app.main._create_agent_runner", return_value=mock_runner):
        result = main._try_agent_query("college notes")

    assert result is None


def test_handle_query_end_to_end_with_agent_query_result():
    from app import main

    agent_result = QueryResult(
        query="college notes",
        intent=QueryIntent("document_search", 1.0, {"method": "semantic_chunks"}),
        answer_type="documents",
        data=[{"document": "Operating Systems notes"}],
    )
    completed_state = AgentState(
        query="college notes",
        status="completed",
        tool_calls=[
            ToolCall(
                tool_name="document_search",
                arguments={"query": "college notes"},
                result=agent_result,
            )
        ],
    )

    mock_runner = MagicMock()
    mock_runner.run.return_value = completed_state

    with patch(
        "app.main._create_agent_runner",
        return_value=mock_runner,
    ), patch(
        "app.main._format_document_result",
        return_value="Agent answer",
    ) as format_document:
        response = main.handle_query("college notes")

    assert response == "Agent answer"
    format_document.assert_called_once_with(agent_result)


def test_handle_query_falls_back_to_route_intelligent_when_agent_returns_none():
    from app import main

    # Reset any previous session state to avoid cross-test interference
    main.LAST_RESULT = None

    fallback_result = QueryResult(
        query="some complex intent",
        intent="unknown",
        answer_type="unknown",
        data=None,
    )

    with patch(
        "app.main._try_agent_query",
        return_value=None,
    ), patch(
        "app.main.route_intelligent",
        return_value=fallback_result,
    ) as mock_route:
        response = main.handle_query("some complex intent")

    mock_route.assert_called_once_with("some complex intent")
    assert "don't have a tool for it yet" in response
