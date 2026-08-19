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
from pathlib import Path

if __package__ is None:
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parent.parent)
    )
    

from intelligence.router import route_intelligent
from prompting.prompt_builder import build_prompt
from llm.generator import generate_response


LAST_RESULT = None


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
    """Answer questions from retrieved documents."""

    retrieved_chunks = [
        item["document"]
        for item in result.data
        if item.get("document")
    ]

    if not retrieved_chunks:
        return "I couldn't find relevant information in your documents."

    prompt = build_prompt(
        result.query,
        retrieved_chunks,
    )

    return generate_response(prompt)


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


def handle_query(query):
    """
    Main LifeOS query handler.
    """

    global LAST_RESULT

    result = route_intelligent(query)

    if result.answer_type != "unknown":

        LAST_RESULT = result

    if result.answer_type == "files":
        LAST_RESULT = result

        return _format_file_results(result)

    if result.answer_type == "time":
        return _format_time_result(result)

    if result.answer_type == "structured_result":
        return _format_structured_result(result)

    if result.answer_type == "schedule_context":
        return _format_schedule_result(result)

    if result.answer_type == "documents":
        return _format_document_result(result)

    if result.answer_type == "structured_files":
        return _format_structured_files(result)

    if result.answer_type == "people":
        return _format_people_result(result)

    # Follow-up query handling
    if result.answer_type == "unknown" and LAST_RESULT:

        if LAST_RESULT.answer_type == "files":

            last_item = LAST_RESULT.data[0]

            if isinstance(last_item, dict):
                file_query = last_item.get("filename") or last_item.get("source")
            else:
                file_query = None

            from query.router import _document_search

            return _format_document_result(
                _document_search(file_query)
            )

        return _format_document_result(LAST_RESULT)

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


if __name__ == "__main__":
    main()
