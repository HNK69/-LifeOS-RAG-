from app.context.context_store import (
    set_context,
    get_context,
    get_all_context,
    clear_context,
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