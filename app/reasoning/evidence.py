"""
evidence.py

Evidence normalization, structuring, and validation pipeline for LifeOS.
"""
from __future__ import annotations

from typing import Any

from .confidence import calculate_calibrated_confidence
from .conflicts import detect_all_conflicts


def _extract_temporal_context(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract temporal metadata from personal context.
    Correctly unpacks nested 'context' dictionaries produced by compose_context().
    """
    temporal: list[dict[str, Any]] = []

    personal_context = data.get("personal_context", {})
    if not isinstance(personal_context, dict):
        return temporal

    # If nested under 'context' (standard compose_context return format), unpack it
    if isinstance(personal_context.get("context"), dict):
        items_map = personal_context["context"]
    else:
        items_map = personal_context

    for key, item in items_map.items():
        if not isinstance(item, dict):
            continue

        temporal.append({
            "key": key,
            "value": item.get("value"),
            "context_type": item.get("context_type"),
            "updated_at": item.get("updated_at"),
            "valid_from": item.get("valid_from"),
            "valid_until": item.get("valid_until"),
        })

    return temporal


def _detect_conflicts(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Backward-compatible deterministic conflict detection."""
    return detect_all_conflicts(evidence)


def _calculate_confidence(evidence: dict[str, Any]) -> float:
    """Backward-compatible deterministic calibrated confidence calculation."""
    return calculate_calibrated_confidence(evidence)


def build_evidence(result: Any) -> dict[str, Any]:
    """
    Normalize any QueryResult or composite retrieval into a unified reasoning evidence package.
    """
    data = result.data

    evidence: dict[str, Any] = {
        "query": getattr(result, "query", ""),
        "intent": (
            result.intent.name
            if hasattr(result.intent, "name")
            else (
                result.intent.intent
                if hasattr(result.intent, "intent")
                else str(getattr(result, "intent", "unknown"))
            )
        ),
        "answer_type": getattr(result, "answer_type", "unknown"),
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

    # 1. Document chunks
    if evidence["answer_type"] == "documents":
        evidence["sources"] = [
            {
                "source": item.get("source"),
                "file_path": item.get("file_path"),
                "content": item.get("document"),
                "distance": item.get("distance"),
                "rerank_score": item.get("rerank_score"),
                "chunk_id": item.get("chunk_id"),
            }
            for item in data
            if isinstance(item, dict) and item.get("document")
        ]

    # 2. Schedule & Time Context
    elif evidence["answer_type"] == "schedule_context":
        evidence["sources"] = [
            {
                "source": item.get("source"),
                "file_path": item.get("file_path"),
                "content": item.get("document"),
                "distance": item.get("distance"),
                "rerank_score": item.get("rerank_score"),
            }
            for item in data.get("documents", [])
            if isinstance(item, dict) and item.get("document")
        ]

        time_context = data.get("time_context", {})
        raw_personal_ctx = time_context.get("personal_context", {})
        evidence["personal_context"] = raw_personal_ctx

        if isinstance(raw_personal_ctx, dict):
            rel_items = raw_personal_ctx.get("relationships", [])
            if rel_items:
                for r in rel_items:
                    if r not in evidence["relationships"]:
                        evidence["relationships"].append(r)

    # 3. Structured Data
    elif evidence["answer_type"] == "structured_result":
        evidence["structured_data"] = [
            {
                "dataset": data.get("dataset"),
                "result": data.get("result"),
            }
        ]

    # 4. Entity Relationships
    elif evidence["answer_type"] == "relationships":
        if isinstance(data, list):
            evidence["relationships"] = data
        elif isinstance(data, dict):
            evidence["relationships"] = data.get("relationships", [data])

    # 5. Multimodal Evidence
    elif evidence["answer_type"] == "multimodal":
        evidence["multimodal"] = data if isinstance(data, list) else [data]

    # 6. Multi-Source / Composite Aggregation
    elif evidence["answer_type"] in {"multi_source", "composite"}:
        if isinstance(data, dict):
            if data.get("documents"):
                evidence["sources"].extend([
                    {
                        "source": item.get("source"),
                        "file_path": item.get("file_path"),
                        "content": item.get("document"),
                        "distance": item.get("distance"),
                        "rerank_score": item.get("rerank_score"),
                    }
                    for item in data["documents"]
                    if isinstance(item, dict) and item.get("document")
                ])
            if data.get("personal_context"):
                evidence["personal_context"] = data["personal_context"]
            if data.get("relationships"):
                for r in data["relationships"]:
                    if r not in evidence["relationships"]:
                        evidence["relationships"].append(r)
            if data.get("structured_data"):
                evidence["structured_data"] = data["structured_data"]
            if data.get("multimodal"):
                evidence["multimodal"] = data["multimodal"]

    # Extract auxiliary relationships embedded in dict data
    if isinstance(data, dict) and "relationships" in data:
        for r in data["relationships"]:
            if r not in evidence["relationships"]:
                evidence["relationships"].append(r)

    # Extract temporal context from personal context if present
    if evidence.get("personal_context"):
        temporal = _extract_temporal_context(evidence)
        for t in temporal:
            if t not in evidence["temporal_context"]:
                evidence["temporal_context"].append(t)

    # Deterministic conflict detection
    evidence["conflicts"] = _detect_conflicts(evidence)

    # Calibrated confidence scoring
    evidence["confidence"] = _calculate_confidence(evidence)

    return evidence
