from typing import Any

def _extract_temporal_context(data):
    """Extract temporal metadata from personal context."""
    temporal = []

    personal_context = data.get("personal_context", {})
    

    for key, item in personal_context.items():
        if not isinstance(item, dict):
            continue

        temporal.append(
            {
                "key": key,
                "value": item.get("value"),
                "context_type": item.get("context_type"),
                "updated_at": item.get("updated_at"),
                "valid_from": item.get("valid_from"),
                "valid_until": item.get("valid_until"),
            }
        )

    return temporal

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
        "temporal_context": [],
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
        evidence["temporal_context"] = _extract_temporal_context(
            evidence
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

