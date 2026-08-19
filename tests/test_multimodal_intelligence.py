import sys
sys.path.insert(0, ".")
sys.path.insert(0, "app")

from app.intelligence.models import IntentPlan
from app.intelligence import router


def test_multimodal_search_execution(monkeypatch):
    expected = [
        {
            "source": "photo.jpg",
            "file_path": "/photos/photo.jpg",
            "document_type": "media",
        },
        {
            "source": "report.pdf",
            "file_path": "/docs/report.pdf",
            "document_type": "document",
        },
    ]

    monkeypatch.setattr(
        "retrieval.multimodal_retriever.search_multimodal",
        lambda query, top_k=20: expected,
    )

    monkeypatch.setattr(
    router,
    "search_multimodal",
    lambda query, top_k=20: expected,
    )

    plan = IntentPlan(
        intent="multimodal_search",
        confidence=1.0,
    )

    result = router.execute_plan(
        "find related information",
        plan,
    )

    assert result.answer_type == "multimodal"
    assert result.data == expected