"""
conflicts.py

Deterministic conflict and contradiction detection across evidence items for LifeOS.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def detect_temporal_conflicts(temporal_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Detect conflicting schedule and temporal context items.

    Checks:
    1. Exact key multiple distinct values.
    2. Semantic key families (e.g. current_class vs current_class_alt).
    3. Overlapping time intervals with contradictory values.
    """
    conflicts: list[dict[str, Any]] = []

    # 1. Group by exact key
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in temporal_items:
        key = item.get("key")
        if key:
            by_key.setdefault(key, []).append(item)

    for key, items in by_key.items():
        distinct_values = {
            str(item.get("value"))
            for item in items
            if item.get("value") is not None
        }
        if len(distinct_values) > 1:
            conflicts.append({
                "conflict_type": "key_value_mismatch",
                "key": key,
                "description": f"Conflicting values detected for context key '{key}': {sorted(distinct_values)}",
                "conflicting_values": sorted(distinct_values),
                "evidence_items": items,
            })

    # 2. Check schedule items for concurrent conflicting values or overlapping validity
    schedule_items = [
        item for item in temporal_items
        if item.get("context_type") == "schedule" or "class" in str(item.get("key", "")).lower() or "schedule" in str(item.get("key", "")).lower()
    ]

    for i in range(len(schedule_items)):
        for j in range(i + 1, len(schedule_items)):
            item_a = schedule_items[i]
            item_b = schedule_items[j]

            val_a = str(item_a.get("value", "")).strip()
            val_b = str(item_b.get("value", "")).strip()

            if not val_a or not val_b or val_a.lower() == val_b.lower():
                continue

            # Check if keys belong to the same concept (e.g. current_class, current_class_alt)
            key_a = str(item_a.get("key", "")).lower()
            key_b = str(item_b.get("key", "")).lower()
            stem_a = key_a.split("_")[0]
            stem_b = key_b.split("_")[0]

            same_concept = (
                key_a == key_b
                or (key_a.startswith("current_") and key_b.startswith("current_"))
                or (stem_a and stem_a == stem_b)
            )

            # Check time interval overlap
            start_a = _parse_iso(item_a.get("valid_from"))
            end_a = _parse_iso(item_a.get("valid_until"))
            start_b = _parse_iso(item_b.get("valid_from"))
            end_b = _parse_iso(item_b.get("valid_until"))

            time_overlaps = False
            if start_a and end_a and start_b and end_b:
                time_overlaps = (start_a < end_b) and (start_b < end_a)
            elif (not start_a and not end_a) or (not start_b and not end_b):
                # When no explicit window is set, concurrent presence of current_* keys is a conflict
                time_overlaps = same_concept

            if same_concept or time_overlaps:
                conflict_record = {
                    "conflict_type": "schedule_overlap",
                    "key": f"{item_a.get('key')} vs {item_b.get('key')}",
                    "description": f"Conflicting concurrent schedule facts: '{val_a}' vs '{val_b}'",
                    "conflicting_values": sorted([val_a, val_b]),
                    "evidence_items": [item_a, item_b],
                }
                # Prevent duplicate conflict records
                if not any(c.get("description") == conflict_record["description"] for c in conflicts):
                    conflicts.append(conflict_record)

    return conflicts


def detect_relationship_conflicts(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect contradictory or cyclic hierarchical relationships between entities."""
    conflicts: list[dict[str, Any]] = []

    pairs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in relationships:
        src = str(
            r.get("source_name")
            or (r.get("source", {}).get("canonical_name") if isinstance(r.get("source"), dict) else None)
            or (r.get("source", {}).get("name") if isinstance(r.get("source"), dict) else str(r.get("source", "")))
        ).strip().lower()

        tgt = str(
            r.get("target_name")
            or (r.get("target", {}).get("canonical_name") if isinstance(r.get("target"), dict) else None)
            or (r.get("target", {}).get("name") if isinstance(r.get("target"), dict) else str(r.get("target", "")))
        ).strip().lower()

        if src and tgt:
            pairs.setdefault((src, tgt), []).append(r)

    # Check for symmetric hierarchy contradictions (e.g. A reports_to B and B reports_to A)
    hierarchical_types = {"reports_to", "manages", "parent_of", "supervisor_of"}
    seen_contradictions = set()

    for (src, tgt), items in pairs.items():
        reverse_items = pairs.get((tgt, src), [])
        for item in items:
            rel_type = str(item.get("relationship_type", "")).strip().lower()
            if rel_type in hierarchical_types:
                for rev in reverse_items:
                    rev_type = str(rev.get("relationship_type", "")).strip().lower()
                    if rev_type == rel_type:
                        pair_key = tuple(sorted([src, tgt]))
                        if pair_key not in seen_contradictions:
                            seen_contradictions.add(pair_key)
                            conflicts.append({
                                "conflict_type": "relationship_contradiction",
                                "key": f"{src} <-> {tgt}",
                                "description": f"Contradictory reciprocal hierarchical relationship '{rel_type}' between '{src}' and '{tgt}'",
                                "conflicting_values": [rel_type, rev_type],
                                "evidence_items": [item, rev],
                            })

    return conflicts


def detect_all_conflicts(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Consolidated deterministic conflict detection across all evidence fields."""
    conflicts: list[dict[str, Any]] = []

    temporal_items = evidence.get("temporal_context", [])
    if temporal_items:
        conflicts.extend(detect_temporal_conflicts(temporal_items))

    rel_items = evidence.get("relationships", [])
    if rel_items:
        conflicts.extend(detect_relationship_conflicts(rel_items))

    return conflicts
