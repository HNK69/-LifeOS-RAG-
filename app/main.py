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

from query.router import route_query
from prompting.prompt_builder import build_prompt
from llm.generator import generate_response


def _format_file_results(result):
    """Format discovered files for the user/UI."""

    files = result.data

    if not files:
        return "I couldn't find a matching file."

    lines = ["I found these files:"]

    for index, file in enumerate(files, start=1):
        filename = file.get("filename", "Unknown file")
        path = str(file.get("path", "")).replace("\\", "/")

        lines.append(
            f"{index}. {filename}"
        )
        lines.append(
            f"   Path: {path}"
        )

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
    """
    Give the LLM schedule context plus retrieved timetable
    information so it can produce the actual answer.
    """

    data = result.data

    documents = data.get(
        "documents",
        [],
    )

    retrieved_chunks = [
        item["document"]
        for item in documents
        if item.get("document")
    ]

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


def handle_query(query):
    """
    Main LifeOS query handler.

    Returns a user-facing response.
    """

    result = route_query(query)

    if result.answer_type == "files":
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
        files = result.data

        if not files:
            return "I couldn't find a matching dataset."

        lines = ["I found these datasets:"]

        for index, file in enumerate(
            files,
            start=1,
        ):
            lines.append(
                f"{index}. {file['filename']}"
            )

        return "\n".join(lines)

    return (
        "I understood the request, but "
        "I don't have a tool for it yet."
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


if __name__ == "__main__":
    main()