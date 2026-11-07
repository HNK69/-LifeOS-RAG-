"""
test_phase9_conflicts_temporal_relational.py

Tests covering deterministic conflict detection, relative-time resolution,
and bounded multi-hop relationship traversal for Phase 9.
"""
from datetime import datetime, timezone

from app.knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
)
from app.knowledge.relationship_retriever import traverse_relationships
from app.reasoning.conflicts import (
    detect_all_conflicts,
    detect_relationship_conflicts,
    detect_temporal_conflicts,
)
from app.reasoning.temporal import (
    extract_temporal_expression,
    resolve_relative_time,
)


def setup_function():
    clear_entity_store()


def teardown_function():
    clear_entity_store()


# ---------------------------------------------------------------------
# CONFLICT DETECTION TESTS
# ---------------------------------------------------------------------

def test_detects_schedule_overlap_conflict():
    """Two schedule entries with overlapping validity and different values must be detected as conflict."""
    items = [
        {
            "key": "lecture",
            "value": "DBMS Lecture",
            "context_type": "schedule",
            "valid_from": "2026-11-07T10:00:00+00:00",
            "valid_until": "2026-11-07T12:00:00+00:00",
        },
        {
            "key": "lab",
            "value": "Operating Systems Lab",
            "context_type": "schedule",
            "valid_from": "2026-11-07T11:00:00+00:00",
            "valid_until": "2026-11-07T13:00:00+00:00",
        },
    ]

    conflicts = detect_temporal_conflicts(items)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["conflict_type"] == "schedule_overlap"
    assert "DBMS Lecture" in c["conflicting_values"]
    assert "Operating Systems Lab" in c["conflicting_values"]


def test_detects_hierarchical_relationship_contradiction():
    """Reciprocal hierarchical relationships (A reports_to B and B reports_to A) are contradictory."""
    relationships = [
        {
            "source_name": "Alice",
            "target_name": "Bob",
            "relationship_type": "reports_to",
            "confidence": 0.9,
        },
        {
            "source_name": "Bob",
            "target_name": "Alice",
            "relationship_type": "reports_to",
            "confidence": 0.85,
        },
    ]

    conflicts = detect_relationship_conflicts(relationships)

    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "relationship_contradiction"
    assert "alice" in conflicts[0]["key"].lower()
    assert "bob" in conflicts[0]["key"].lower()


def test_clean_evidence_produces_no_conflicts():
    """Non-contradictory evidence returns an empty list of conflicts."""
    items = [
        {
            "key": "lecture",
            "value": "DBMS",
            "context_type": "schedule",
            "valid_from": "2026-11-07T09:00:00+00:00",
            "valid_until": "2026-11-07T10:00:00+00:00",
        },
        {
            "key": "lab",
            "value": "DBMS Lab",
            "context_type": "schedule",
            "valid_from": "2026-11-07T10:00:00+00:00",
            "valid_until": "2026-11-07T11:00:00+00:00",
        },
    ]

    conflicts = detect_temporal_conflicts(items)
    assert conflicts == []


# ---------------------------------------------------------------------
# TEMPORAL RESOLUTION TESTS
# ---------------------------------------------------------------------

def test_resolve_relative_time_today_yesterday_tomorrow():
    ref = datetime(2026, 11, 7, 14, 30, tzinfo=timezone.utc)

    today = resolve_relative_time("today", reference_time=ref)
    assert today["target_date"] == "2026-11-07"
    assert "2026-11-07T00:00:00" in today["valid_from"]
    assert "2026-11-07T23:59:59" in today["valid_until"]

    yesterday = resolve_relative_time("yesterday", reference_time=ref)
    assert yesterday["target_date"] == "2026-11-06"

    tomorrow = resolve_relative_time("tomorrow", reference_time=ref)
    assert tomorrow["target_date"] == "2026-11-08"


def test_resolve_relative_time_next_weekday():
    # 2026-11-07 is Saturday
    ref = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

    next_mon = resolve_relative_time("next monday", reference_time=ref)
    # Next Monday after Saturday Nov 7 is Nov 9
    assert next_mon["target_date"] == "2026-11-09"
    assert next_mon["is_relative"] is True


def test_resolve_explicit_time_window():
    ref = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)

    window = resolve_relative_time("between 9 AM and 11 AM", reference_time=ref)
    assert "09:00:00" in window["valid_from"]
    assert "11:00:00" in window["valid_until"]


def test_extract_temporal_expression_from_query():
    q1 = "What classes do I have between 10 AM and 12 PM?"
    assert extract_temporal_expression(q1) == "between 10 AM and 12 PM"

    q2 = "Who do I meet next Friday?"
    assert extract_temporal_expression(q2) == "next Friday"

    q3 = "What was my schedule yesterday?"
    assert extract_temporal_expression(q3) == "yesterday"


# ---------------------------------------------------------------------
# MULTI-HOP RELATIONSHIP TRAVERSAL TESTS
# ---------------------------------------------------------------------

def test_bounded_multi_hop_traversal():
    """Traverse 2 hops: Alice -> works_with -> Bob -> manages -> Charlie."""
    alice = create_entity("person", "Alice Smith")
    bob = create_entity("person", "Bob Jones")
    charlie = create_entity("person", "Charlie Brown")

    create_relationship(alice["id"], bob["id"], "works_with", confidence=0.9)
    create_relationship(bob["id"], charlie["id"], "manages", confidence=0.8)

    # 1-hop traversal
    hops_1 = traverse_relationships("Alice Smith", max_hops=1, direction="outgoing")
    assert len(hops_1) == 1
    assert hops_1[0]["target_entity"] == "Bob Jones"
    assert hops_1[0]["depth"] == 1

    # 2-hop traversal
    hops_2 = traverse_relationships("Alice Smith", max_hops=2, direction="outgoing")
    assert len(hops_2) == 2
    assert any(h["target_entity"] == "Charlie Brown" and h["depth"] == 2 for h in hops_2)

    charlie_step = next(h for h in hops_2 if h["target_entity"] == "Charlie Brown")
    assert charlie_step["path"] == ["Alice Smith", "works_with", "Bob Jones", "manages", "Charlie Brown"]
    # Cumulative confidence: 0.9 * 0.8 = 0.72
    assert charlie_step["confidence"] == 0.72


def test_multi_hop_cycle_prevention():
    """Cycles (Alice -> Bob -> Alice) must not cause infinite loops or duplicate paths."""
    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    create_relationship(alice["id"], bob["id"], "knows", confidence=0.9)
    create_relationship(bob["id"], alice["id"], "knows", confidence=0.9)

    # With max_hops=3, cycle should terminate gracefully
    results = traverse_relationships("Alice", max_hops=3, direction="outgoing")
    assert len(results) <= 2
    # Ensure Alice is not re-visited as target in path history
    for r in results:
        assert r["path"].count("Alice") == 1
