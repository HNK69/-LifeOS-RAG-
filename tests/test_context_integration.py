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

def test_schedule_query_passes_personal_context_to_classifier(monkeypatch):
    clear_context()

    set_context(
        "current_class",
        "DBMS",
        context_type="schedule",
        valid_from="2000-01-01T00:00:00+00:00",
    )

    captured = {}

    def fake_classify_sources(query, current_context, candidates):
        captured["context"] = current_context
        return '{"candidates": []}'

    monkeypatch.setattr(
        router,
        "classify_sources",
        fake_classify_sources,
    )

    router._schedule_query("Which class do I have now?")

    assert "personal_context" in captured["context"]
    assert (
        captured["context"]["personal_context"]["context"]
        ["current_class"]["value"]
        == "DBMS"
    )

    clear_context()