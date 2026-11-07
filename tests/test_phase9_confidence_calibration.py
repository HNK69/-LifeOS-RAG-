"""
test_phase9_confidence_calibration.py

Tests proving that evidence quality signals (vector distance, freshness,
conflicts, corroboration) deterministically calibrate confidence scoring.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.reasoning.confidence import calculate_calibrated_confidence
from app.reasoning.evidence import build_evidence
from query.router import QueryIntent, QueryResult


def test_vector_distance_calibrates_confidence():
    """Lower vector distance (closer match) produces higher confidence than large distance."""
    high_match = QueryResult(
        query="Database normalization",
        intent=QueryIntent("document_search", 1.0, {}),
        answer_type="documents",
        data=[
            {
                "document": "3NF reduces redundancy.",
                "source": "db.pdf",
                "distance": 0.10,  # Highly relevant match
            }
        ],
    )

    low_match = QueryResult(
        query="Database normalization",
        intent=QueryIntent("document_search", 1.0, {}),
        answer_type="documents",
        data=[
            {
                "document": "Some distant mention of storage.",
                "source": "misc.pdf",
                "distance": 1.50,  # Poor match
            }
        ],
    )

    ev_high = build_evidence(high_match)
    ev_low = build_evidence(low_match)

    assert ev_high["confidence"] > ev_low["confidence"]
    assert ev_high["confidence"] >= 0.88
    assert ev_low["confidence"] <= 0.45


def test_chunk_count_diversity_bonus():
    """Multiple agreeing chunks provide higher confidence than a single chunk with the same distance."""
    single_chunk = {
        "sources": [{"source": "doc1.pdf", "distance": 0.25, "content": "Fact"}]
    }
    multi_chunk = {
        "sources": [
            {"source": "doc1.pdf", "distance": 0.25, "content": "Fact"},
            {"source": "doc2.pdf", "distance": 0.25, "content": "Fact corroboration"},
            {"source": "doc3.pdf", "distance": 0.25, "content": "Fact third source"},
        ]
    }

    c_single = calculate_calibrated_confidence(single_chunk)
    c_multi = calculate_calibrated_confidence(multi_chunk)

    assert c_multi > c_single


def test_temporal_freshness_decays_confidence():
    """Fresh context (< 24h) yields higher confidence than aged context (> 30 days)."""
    now = datetime.now(timezone.utc)
    fresh_time = (now - timedelta(hours=2)).isoformat()
    stale_time = (now - timedelta(days=40)).isoformat()

    fresh_evidence = {
        "temporal_context": [
            {"key": "location", "value": "Library", "updated_at": fresh_time}
        ]
    }

    stale_evidence = {
        "temporal_context": [
            {"key": "location", "value": "Library", "updated_at": stale_time}
        ]
    }

    c_fresh = calculate_calibrated_confidence(fresh_evidence)
    c_stale = calculate_calibrated_confidence(stale_evidence)

    assert c_fresh > c_stale
    assert c_fresh == 0.95
    assert c_stale == 0.75


def test_conflict_heavily_penalizes_confidence():
    """Presence of detected conflicts reduces confidence by a compounding penalty factor."""
    clean_evidence = {
        "sources": [{"source": "doc.pdf", "distance": 0.2, "content": "Class at 10 AM"}],
        "conflicts": [],
    }

    conflicted_evidence = {
        "sources": [{"source": "doc.pdf", "distance": 0.2, "content": "Class at 10 AM"}],
        "conflicts": [
            {
                "conflict_type": "schedule_overlap",
                "description": "DBMS vs OS conflict",
            }
        ],
    }

    c_clean = calculate_calibrated_confidence(clean_evidence)
    c_conflicted = calculate_calibrated_confidence(conflicted_evidence)

    assert c_conflicted < c_clean
    # Approximately 30% reduction
    assert round(c_clean * 0.70, 2) == round(c_conflicted, 2)


def test_multi_source_corroboration_bonus():
    """Multi-source evidence combining documents, context, and relationships earns a corroboration bonus."""
    single_source = {
        "sources": [{"source": "doc.pdf", "content": "Information"}],
    }

    multi_source = {
        "sources": [{"source": "doc.pdf", "content": "Information"}],
        "relationships": [{"relationship_type": "knows", "source": "Alice", "target": "Bob"}],
        "temporal_context": [{"key": "status", "value": "Active"}],
    }

    c_single = calculate_calibrated_confidence(single_source)
    c_multi = calculate_calibrated_confidence(multi_source)

    assert c_multi > c_single
