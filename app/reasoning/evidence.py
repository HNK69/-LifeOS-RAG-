from typing import Any


def _detect_conflicts(evidence):
    """Detect conflicting values for the same semantic evidence key."""
    conflicts = []
    values_by_key = {}

    for item in evidence.get("temporal_context", []):
        key = item.get("key")
        if not key:
            continue

        values_by_key.setdefault(key, []).append(item)

    for key, items in values_by_key.items():
        values = {
            str(item.get("value"))
            for item in items
            if item.get("value") is not None
        }

        if len(values) > 1:
            conflicts.append({
                "key": key,
                "values": sorted(values),
                "evidence": items,
            })

    return conflicts



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

def _calculate_confidence(evidence):
    """Calculate deterministic confidence for the assembled evidence."""
    signals = []

    if evidence.get("sources"):
        signals.append(0.8)

    if evidence.get("structured_data"):
        signals.append(0.9)

    if evidence.get("relationships"):
        signals.append(0.9)

    if evidence.get("multimodal"):
        signals.append(0.8)

    if evidence.get("personal_context"):
        signals.append(0.9)

    if evidence.get("temporal_context"):
        signals.append(0.9)

    if not signals:
        return 0.0

    confidence = sum(signals) / len(signals)

    if evidence.get("conflicts"):
        confidence *= 0.5

    return round(max(0.0, min(confidence, 1.0)), 3)


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
        "conflicts": [],
        "confidence": 0.0,
    }


    if not data:
        evidence["confidence"] = 0.0
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
        evidence["conflicts"] = _detect_conflicts(
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

    
    evidence["confidence"] = _calculate_confidence(evidence)

    return evidence 

