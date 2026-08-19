import sys
sys.path.insert(0, ".")
sys.path.insert(0, "app")


def test_people_search_execution(monkeypatch):
    from app.intelligence import router
    from app.intelligence.models import IntentPlan

    expected = [
        {
            "source": "photo.jpg",
            "file_path": "/photos/photo.jpg",
            "label": "Test Person",
            "type": "person",
        }
    ]

    monkeypatch.setattr(
        router,
        "retrieve_by_person",
        lambda label: expected,
    )

    plan = IntentPlan(
        intent="people_search",
        confidence=1.0,
        arguments={
            "person_labels": ["Test Person"],
        },
    )

    result = router.execute_plan(
        "Show me photos with Test Person",
        plan,
    )

    assert result.answer_type == "people"
    assert result.data == expected