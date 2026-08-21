from datetime import datetime
from app.context.context_store import (
    set_context,
    get_context,
    get_all_context,
    clear_context,
    get_context_by_type,
    get_active_context,
)


def test_context_store_round_trip():
    clear_context()

    set_context("current_location", "college")

    assert get_context("current_location") == "college"


def test_context_default():
    clear_context()

    assert get_context("unknown", "default") == "default"


def test_context_snapshot():
    clear_context()

    set_context("routine", "study")

    context = get_all_context()

    assert context["routine"]["value"] == "study"
    assert "updated_at" in context["routine"]

    clear_context()

def test_context_persists_across_connections():
    clear_context()

    set_context("location", "college")

    # Force a fresh database connection.
    import app.context.context_store as store

    store._connect().close()

    assert get_context("location") == "college"

    clear_context()

def test_typed_context():
    clear_context()

    set_context(
        "study_time",
        "8 PM",
        context_type="routine",
    )

    context = get_all_context()

    assert context["study_time"]["value"] == "8 PM"
    assert context["study_time"]["context_type"] == "routine"

    clear_context()

def test_get_context_by_type():
    clear_context()

    set_context("study_time", "8 PM", context_type="routine")
    set_context("theme", "dark", context_type="preference")

    routines = get_context_by_type("routine")

    assert "study_time" in routines
    assert routines["study_time"]["value"] == "8 PM"
    assert routines["study_time"]["context_type"] == "routine"
    assert "theme" not in routines

    clear_context()

def test_temporal_context():
    clear_context()

    set_context(
        "study_class",
        "DBMS",
        context_type="schedule",
        valid_from="2026-08-21T08:00:00+05:30",
        valid_until="2026-08-21T10:00:00+05:30",
    )

    context = get_all_context()

    assert context["study_class"]["value"] == "DBMS"
    assert context["study_class"]["valid_from"] == "2026-08-21T08:00:00+05:30"
    assert context["study_class"]["valid_until"] == "2026-08-21T10:00:00+05:30"

    clear_context()

def test_active_context():
    clear_context()

    set_context(
        "current_class",
        "DBMS",
        context_type="schedule",
        valid_from="2026-08-21T08:00:00+05:30",
        valid_until="2026-08-21T10:00:00+05:30",
    )

    active = get_active_context(
        datetime.fromisoformat("2026-08-21T09:00:00+05:30")
    )

    assert active["current_class"]["value"] == "DBMS"

    clear_context()


def test_inactive_context_is_excluded():
    clear_context()

    set_context(
        "current_class",
        "DBMS",
        context_type="schedule",
        valid_from="2026-08-21T08:00:00+05:30",
        valid_until="2026-08-21T10:00:00+05:30",
    )

    active = get_active_context(
        datetime.fromisoformat("2026-08-21T11:00:00+05:30")
    )

    assert "current_class" not in active

    clear_context()

def test_active_context_integrates_with_schedule_time():
    clear_context()

    set_context(
        "current_class",
        "DBMS",
        context_type="schedule",
        valid_from="2026-08-21T08:00:00+05:30",
        valid_until="2026-08-21T10:00:00+05:30",
    )

    active = get_active_context(
        datetime.fromisoformat("2026-08-21T09:00:00+05:30")
    )

    assert active["current_class"]["value"] == "DBMS"

    clear_context()