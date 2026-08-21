from app.context.context_store import clear_context, set_context
from app.query import router


def test_schedule_query_includes_active_personal_context(monkeypatch):
    clear_context()

    set_context(
        "current_class",
        "DBMS",
        context_type="schedule",
        valid_from="2000-01-01T00:00:00+00:00",
    )

    monkeypatch.setattr(
        router,
        "datetime",
        __import__("datetime").datetime,
    )

    monkeypatch.setattr(
        router,
        "classify_sources",
        lambda query, context, candidates: '{"candidates": []}',
    )

    result = router._schedule_query("Which class do I have now?")

    assert (
        result.data["time_context"]["personal_context"]["context"]
        ["current_class"]["value"]
        == "DBMS"
    )

    clear_context()