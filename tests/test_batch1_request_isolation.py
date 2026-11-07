import concurrent.futures
from unittest.mock import patch

from app import main
from query.router import QueryIntent, QueryResult


def test_empty_previous_result_does_not_raise_index_error():
    """BUG-03 Regression: Empty files result must not raise IndexError on follow-up."""
    main.clear_session()

    empty_files_result = QueryResult(
        query="show me files",
        intent=QueryIntent(name="file_discovery", confidence=0.9, arguments={}),
        answer_type="files",
        data=[],  # Empty data list!
    )

    with patch("app.main._try_agent_query", return_value=empty_files_result):
        resp1 = main.handle_query("show me files")
        assert "couldn't find a matching file" in resp1

    unknown_follow_up = QueryResult(
        query="what about it?",
        intent="unknown",
        answer_type="unknown",
        data=None,
    )

    with patch("app.main._try_agent_query", return_value=None), patch(
        "app.main.route_intelligent", return_value=unknown_follow_up
    ):
        # Must not raise IndexError!
        resp2 = main.handle_query("what about it?")
        assert "couldn't find a matching file from your previous request" in resp2


def test_concurrent_request_isolation():
    """CRIT-P4-01: Two concurrent threads/contexts must not leak LAST_RESULT."""
    main.clear_session()

    result_user_a = QueryResult(
        query="find user a file",
        intent=QueryIntent(name="file_discovery", confidence=0.95, arguments={}),
        answer_type="files",
        data=[{"filename": "user_a_private.txt", "path": "data/user_a_private.txt"}],
    )

    result_user_b = QueryResult(
        query="find user b file",
        intent=QueryIntent(name="file_discovery", confidence=0.95, arguments={}),
        answer_type="files",
        data=[{"filename": "user_b_private.txt", "path": "data/user_b_private.txt"}],
    )

    unknown_followup = QueryResult(
        query="tell me more",
        intent="unknown",
        answer_type="unknown",
        data=None,
    )

    def run_user_session(user_result, expected_filename):
        # Set first query in this thread context
        with patch("app.main._try_agent_query", return_value=user_result):
            main.handle_query("find file")

        # Now execute follow up in the same thread context
        with patch("app.main._try_agent_query", return_value=None), patch(
            "app.main.route_intelligent", return_value=unknown_followup
        ), patch(
            "query.router._document_search",
            return_value=QueryResult(
                query=expected_filename,
                intent=QueryIntent(name="document_search", confidence=0.9, arguments={}),
                answer_type="documents",
                data=[{"document": f"Content of {expected_filename}"}],
            ),
        ) as mock_doc_search, patch(
            "app.main._format_document_result",
            side_effect=lambda r: f"Read: {r.query}",
        ):
            resp = main.handle_query("tell me more")
            mock_doc_search.assert_called_once_with(expected_filename)
            return resp

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_a = executor.submit(run_user_session, result_user_a, "user_a_private.txt")
        fut_b = executor.submit(run_user_session, result_user_b, "user_b_private.txt")

        res_a = fut_a.result()
        res_b = fut_b.result()

    assert "user_a_private.txt" in res_a
    assert "user_b_private.txt" in res_b


def test_explicit_session_isolation():
    """Explicit session dictionary guarantees isolation across any execution model."""
    session_a = {}
    session_b = {}

    result_a = QueryResult(
        query="find file a",
        intent=QueryIntent(name="file_discovery", confidence=0.95, arguments={}),
        answer_type="files",
        data=[{"filename": "alpha.pdf", "path": "data/alpha.pdf"}],
    )

    result_b = QueryResult(
        query="find file b",
        intent=QueryIntent(name="file_discovery", confidence=0.95, arguments={}),
        answer_type="files",
        data=[{"filename": "beta.pdf", "path": "data/beta.pdf"}],
    )

    with patch("app.main._try_agent_query", return_value=result_a):
        main.handle_query("find file a", session=session_a)

    with patch("app.main._try_agent_query", return_value=result_b):
        main.handle_query("find file b", session=session_b)

    assert session_a["last_result"].data[0]["filename"] == "alpha.pdf"
    assert session_b["last_result"].data[0]["filename"] == "beta.pdf"

    # Follow-up on session_a must resolve alpha.pdf, not beta.pdf
    unknown_followup = QueryResult(
        query="more",
        intent="unknown",
        answer_type="unknown",
        data=None,
    )

    with patch("app.main._try_agent_query", return_value=None), patch(
        "app.main.route_intelligent", return_value=unknown_followup
    ), patch(
        "query.router._document_search",
        return_value=QueryResult(
            query="alpha.pdf",
            intent=QueryIntent(name="document_search", confidence=0.9, arguments={}),
            answer_type="documents",
            data=[],
        ),
    ) as mock_search, patch(
        "app.main._format_document_result",
        return_value="Answer for alpha",
    ):
        resp = main.handle_query("more", session=session_a)
        mock_search.assert_called_once_with("alpha.pdf")
        assert resp == "Answer for alpha"


def test_legacy_module_last_result_property_backward_compatibility():
    """Existing tests and CLI resetting main.LAST_RESULT = None must work as expected."""
    main.LAST_RESULT = None
    assert main.LAST_RESULT is None

    test_res = QueryResult(
        query="q",
        intent="time",
        answer_type="time",
        data={"time": "12:00", "date": "2026-11-07", "weekday": "Saturday"},
    )
    main.LAST_RESULT = test_res
    assert main.LAST_RESULT == test_res

    main.LAST_RESULT = None
    assert main.LAST_RESULT is None
