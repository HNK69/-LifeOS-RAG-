from typing import Any


def build_evidence(result) -> dict[str, Any]:
    """Normalize a QueryResult into a reasoning-ready evidence package."""
    data = result.data

    evidence = {
        "query": result.query,
        "intent": (
            result.intent.name
            if hasattr(result.intent, "name")
            else str(result.intent)
        ),
        "answer_type": result.answer_type,
        "sources": [],
        "structured_data": [],
        "relationships": [],
        "personal_context": {},
        "multimodal": [],
    }

    if not data:
        return evidence

    if result.answer_type == "documents":
        evidence["sources"] = [
            {
                "source": item.get("source"),
                "file_path": item.get("file_path"),
                "content": item.get("document"),
            }
            for item in data
            if item.get("document")
        ]

    elif result.answer_type == "schedule_context":
        evidence["sources"] = [
            {
                "source": item.get("source"),
                "file_path": item.get("file_path"),
                "content": item.get("document"),
            }
            for item in data.get("documents", [])
            if item.get("document")
        ]

        time_context = data.get("time_context", {})
        evidence["personal_context"] = time_context.get(
            "personal_context",
            {},
        )

    elif result.answer_type == "structured_result":
        evidence["structured_data"] = [
            {
                "dataset": data.get("dataset"),
                "result": data.get("result"),
            }
        ]

    elif result.answer_type == "relationships":
        evidence["relationships"] = data

    elif result.answer_type == "multimodal":
        evidence["multimodal"] = data

    return evidence 