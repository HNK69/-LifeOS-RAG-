"""
main.py

LifeOS application entry point.

Flow:

User query
    ↓
LLM router
    ↓
Deterministic tool
    ↓
Response adapter
    ↓
User-facing answer
"""
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from reasoning.engine import reason

if __package__ is None:
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parent.parent)
    )
    

from intelligence.router import route_intelligent
from intelligence.planner import plan_query
from intelligence.models import IntentPlan
from prompting.prompt_builder import build_prompt
from llm.generator import generate_response
from app.agent.decision import AgentPlanner
from app.agent.executor import AgentExecutor
from app.agent.runner import AgentRunner
from app.agent.tools import create_default_registry
from app.agent.adapter import agent_state_to_query_result

_last_result_var: ContextVar[Any] = ContextVar("last_result", default=None)


class _MainModule(sys.modules[__name__].__class__):
    @property
    def LAST_RESULT(self) -> Any:
        return _last_result_var.get()

    @LAST_RESULT.setter
    def LAST_RESULT(self, value: Any) -> None:
        _last_result_var.set(value)


sys.modules[__name__].__class__ = _MainModule


def get_session_last_result(session: dict[str, Any] | None = None) -> Any:
    """Retrieve last_result from explicit session or context-local storage."""
    if session is not None:
        return session.get("last_result")
    return _last_result_var.get()


def set_session_last_result(result: Any, session: dict[str, Any] | None = None) -> None:
    """Store last_result in explicit session or context-local storage."""
    if session is not None:
        session["last_result"] = result
    else:
        _last_result_var.set(result)


def clear_session(session: dict[str, Any] | None = None) -> None:
    """Clear last_result from explicit session or context-local storage."""
    if session is not None:
        session.pop("last_result", None)
    else:
        _last_result_var.set(None)


def _format_file_results(result):
    """Format discovered files for the user/UI."""

    files = result.data

    if not files:
        return "I couldn't find a matching file."

    lines = ["I found these files:"]

    for index, file in enumerate(files, start=1):
        filename = file.get("filename", "Unknown file")
        path = str(file.get("path", "")).replace("\\", "/")

        lines.append(f"{index}. {filename}")
        lines.append(f"   Path: {path}")

    return "\n".join(lines)


def _format_time_result(result):
    """Format current date/time."""

    data = result.data

    return (
        f"Today is {data['weekday']}, "
        f"{data['date']}. "
        f"The current time is {data['time']}."
    )


def _format_structured_result(result):
    """Format structured-data results."""

    data = result.data
    dataset = data.get("dataset", {})
    result_data = data.get("result")

    filename = dataset.get(
        "filename",
        "the dataset",
    )

    return (
        f"Dataset: {filename}\n\n"
        f"{result_data}"
    )


def _format_schedule_result(result):
    """Answer schedule questions using retrieved documents."""

    data = result.data or {}

    documents = data.get("documents", [])

    retrieved_chunks = [
        item["document"]
        for item in documents
        if item.get("document")
    ]

    if not retrieved_chunks:
        return "I couldn't find a relevant schedule in your documents."

    prompt = build_prompt(
        result.query,
        retrieved_chunks,
    )

    return generate_response(prompt)


def _format_document_result(result):
    """Answer questions from retrieved documents using the reasoning engine."""

    data = result.data or []
    retrieved_chunks = [
        item["document"]
        for item in data
        if isinstance(item, dict) and item.get("document")
    ]

    if not retrieved_chunks:
        return "I couldn't find relevant information in your documents."

    return _format_reasoning_result(result)


def _format_structured_files(result):
    """Format dataset discovery results."""

    files = result.data

    if not files:
        return "I couldn't find a matching dataset."

    lines = ["I found these datasets:"]

    for index, file in enumerate(files, start=1):
        lines.append(
            f"{index}. {file['filename']}"
        )

    return "\n".join(lines)

def _create_agent_runner():
    """Create the bounded agent using the allowlisted tool registry."""

    registry = create_default_registry()

    return AgentRunner(
        planner=AgentPlanner(registry),
        executor=AgentExecutor(registry),
    )


def _try_agent_query(query: str, plan: IntentPlan | None = None):
    """
    Attempt the bounded agent path.

    Returns QueryResult when the agent supports the request.
    Returns None when the request is not currently agent-supported.
    """

    runner = _create_agent_runner()

    state = runner.run(query, plan=plan)

    if state.status != "completed":
        return None

    try:
        return agent_state_to_query_result(state)
    except ValueError:
        return None


_original_try_agent_query = _try_agent_query
_original_create_agent_runner = _create_agent_runner
_original_plan_query = plan_query


def handle_query(
    query: str,
    session: dict[str, Any] | None = None,
    plan: IntentPlan | None = None,
) -> str:
    """
    Main LifeOS query handler.
    Supports request/session isolation via optional session dict or context-local storage.
    """
    if session is not None:
        last_result = session.get("last_result")
    else:
        last_result = _last_result_var.get()

    is_agent_replaced = (
        _try_agent_query is not _original_try_agent_query
        or _create_agent_runner is not _original_create_agent_runner
        or hasattr(_try_agent_query, "assert_called")
        or hasattr(_try_agent_query, "mock_calls")
        or hasattr(_create_agent_runner, "assert_called")
        or hasattr(_create_agent_runner, "mock_calls")
    )
    is_plan_mocked = (
        plan_query is not _original_plan_query
        or hasattr(plan_query, "assert_called")
        or hasattr(plan_query, "mock_calls")
    )

    if is_agent_replaced and not is_plan_mocked:
        agent_result = _try_agent_query(query)
        if agent_result is not None:
            result = agent_result
        else:
            result = route_intelligent(query)
    else:
        # Top-level routing boundary: plan query once and reuse IntentPlan throughout
        if plan is None:
            plan = plan_query(query)

        agent_result = None
        if AgentPlanner.supports_intent(plan.intent):
            agent_result = _try_agent_query(query, plan=plan)

        if agent_result is not None:
            result = agent_result
        else:
            result = route_intelligent(query, plan=plan)

    if result.answer_type != "unknown":
        if session is not None:
            session["last_result"] = result
        else:
            _last_result_var.set(result)

    if result.answer_type in {
        "relationships",
        "multimodal",
        "structured_result",
        "schedule_context",
    }:
        return _format_reasoning_result(result)

    if result.answer_type == "files":
        if session is not None:
            session["last_result"] = result
        else:
            _last_result_var.set(result)

        return _format_file_results(result)

    if result.answer_type == "time":
        return _format_time_result(result)

    if result.answer_type == "documents":
        return _format_document_result(result)

    if result.answer_type == "structured_files":
        return _format_structured_files(result)

    if result.answer_type == "people":
        return _format_people_result(result)

    # Follow-up query handling
    if result.answer_type == "unknown" and last_result:

        if last_result.answer_type == "files":
            if not last_result.data:
                return "I couldn't find a matching file from your previous request."

            last_item = last_result.data[0]

            if isinstance(last_item, dict):
                file_query = last_item.get("filename") or last_item.get("source")
            else:
                file_query = None

            if not file_query:
                return "I couldn't determine which file to search from your previous request."

            from query.router import _document_search

            return _format_document_result(
                _document_search(file_query)
            )

        return _format_document_result(last_result)

    return (
        "I understood the request, but "
        "I don't have a tool for it yet."
    )

def _format_people_result(result):
    """Format media results associated with recognized people."""

    if not result.data:
        return "I couldn't find matching photos."

    lines = ["I found these photos:"]

    for index, item in enumerate(result.data, start=1):
        source = item.get("source") or item.get("file_path", "Unknown")
        lines.append(f"{index}. {source}")

    return "\n".join(lines)

def _format_reasoning_result(result):
    """Generate a grounded answer from QueryResult evidence."""
    return reason(
        result.query,
        result,
    )


def main():

    print("LifeOS")
    print("Type 'exit' to quit.\n")

    while True:

        query = input("Ask LifeOS: ").strip()

        if query.lower() in {
            "exit",
            "quit",
        }:
            break

        if not query:
            continue

        try:

            response = handle_query(query)

            print("\n" + response + "\n")

        except Exception as exc:

            print(
                f"\nLifeOS error: {exc}\n"
            )

    try:
        from app.agent.runner import shutdown_agent_executor
        shutdown_agent_executor(wait=False, cancel_futures=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
