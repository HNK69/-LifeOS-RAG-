"""
confidence.py

Deterministic confidence calculation based on real evidence quality signals for LifeOS.

Formula & Tradeoffs:
-------------------
The confidence score C in [0.0, 1.0] is a weighted combination of quality signals
derived deterministically from the assembled evidence package:

1. Retrieval / Vector Distance:
   When document sources expose ChromaDB cosine distances d, similarity is computed as:
   sim = 1.0 / (1.0 + min_distance).
   If distance is not present (e.g. legacy/mock data), a default baseline of 0.8 is used.
   Tradeoff: Allows mock data to pass legacy thresholds while real vector queries dynamically
   scale from ~0.4 (distant chunk) to ~0.95 (near-exact vector match).

2. Knowledge Graph Confidence:
   Entity relationships in SQLite store extraction confidence scores in [0.0, 1.0].
   The average of available confidence scores is used. If absent, baseline is 0.9.

3. Context Freshness & Relevance:
   Temporal and personal context items compute age decay from 'updated_at'. Recent context
   (< 24 hours) receives high confidence (0.95); older context decays gracefully down to 0.75.

4. Source Diversity Corroboration:
   Multiple distinct modalities corroborating a query earn a small deterministic bonus
   (+0.02 per distinct source type, max +0.06).

5. Conflict Penalty:
   Each detected conflict applies a 30% compounding reduction factor: C = C * (0.70 ^ num_conflicts).
   Tradeoff: Preserves inspectability; contradiction heavily penalizes certainty without arbitrary cliff drops.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def calculate_calibrated_confidence(evidence: dict[str, Any]) -> float:
    """Calculate calibrated deterministic confidence for an evidence package."""
    signals: list[float] = []
    distinct_types = 0

    # 1. Document / Vector Sources
    sources = evidence.get("sources", [])
    if sources:
        distinct_types += 1
        distances = [
            float(s["distance"])
            for s in sources
            if isinstance(s, dict) and s.get("distance") is not None
        ]
        if distances:
            best_dist = min(distances)
            # Distance 0 -> 1.0; distance 1.0 -> 0.5; distance 2.0 -> 0.33
            sim = 1.0 / (1.0 + max(0.0, best_dist))
            # Slightly reward multiple agreeing chunks
            chunk_bonus = min(0.05, 0.01 * (len(sources) - 1))
            signals.append(round(min(1.0, sim + chunk_bonus), 3))
        else:
            signals.append(0.8)

    # 2. Structured Data
    structured = evidence.get("structured_data", [])
    if structured:
        distinct_types += 1
        has_result = any(
            isinstance(s, dict) and s.get("result") is not None
            for s in structured
        )
        signals.append(0.9 if has_result else 0.5)

    # 3. Knowledge Graph Relationships
    relationships = evidence.get("relationships", [])
    if relationships:
        distinct_types += 1
        conf_scores = [
            float(r["confidence"])
            for r in relationships
            if isinstance(r, dict) and r.get("confidence") is not None
        ]
        if conf_scores:
            signals.append(round(sum(conf_scores) / len(conf_scores), 3))
        else:
            signals.append(0.9)

    # 4. Multimodal Evidence
    multimodal = evidence.get("multimodal", [])
    if multimodal:
        distinct_types += 1
        mm_distances = [
            float(m["distance"])
            for m in multimodal
            if isinstance(m, dict) and m.get("distance") is not None
        ]
        if mm_distances:
            best_mm = min(mm_distances)
            signals.append(round(1.0 / (1.0 + max(0.0, best_mm)), 3))
        else:
            signals.append(0.8)

    # 5. Personal / Temporal Context
    temporal = evidence.get("temporal_context", [])
    personal = evidence.get("personal_context", {})
    if temporal or personal:
        distinct_types += 1
        # Check freshness of temporal entries
        ages_in_days = []
        now = datetime.now(timezone.utc)
        for item in temporal:
            if isinstance(item, dict) and item.get("updated_at"):
                up_dt = _parse_iso(item.get("updated_at"))
                if up_dt:
                    age_days = max(0.0, (now - up_dt).total_seconds() / 86400.0)
                    ages_in_days.append(age_days)

        if ages_in_days:
            min_age = min(ages_in_days)
            if min_age <= 1.0:
                signals.append(0.95)
            elif min_age <= 7.0:
                signals.append(0.90)
            elif min_age <= 30.0:
                signals.append(0.85)
            else:
                signals.append(0.75)
        else:
            signals.append(0.90)

    if not signals:
        return 0.0

    # Base average across active signals
    base_confidence = sum(signals) / len(signals)

    # Corroboration bonus for multi-source evidence
    if distinct_types > 1:
        corroboration = min(0.06, 0.02 * (distinct_types - 1))
        base_confidence = min(1.0, base_confidence + corroboration)

    # Conflict penalty: each conflict penalizes confidence
    conflicts = evidence.get("conflicts", [])
    if conflicts:
        penalty_factor = 0.70 ** len(conflicts)
        base_confidence *= penalty_factor

    return round(max(0.0, min(base_confidence, 1.0)), 3)
