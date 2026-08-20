import sys

sys.path.insert(0, ".")
sys.path.insert(0, "app")

from retrieval.candidate_resolver import resolve_with_ambiguity


def test_resolve_with_ambiguity_selects_clear_candidate():
    candidates = [
        {
            "document": "project architecture notes",
            "source": "project.txt",
            "distance": 0.1,
            "metadata": {},
        },
        {
            "document": "unrelated notes",
            "source": "other.txt",
            "distance": 0.9,
            "metadata": {},
        },
    ]

    result = resolve_with_ambiguity(
        "project architecture",
        candidates,
    )

    assert result["ambiguous"] is False
    assert result["selected"]["source"] == "project.txt"


def test_resolve_with_ambiguity_detects_close_candidates():
    candidates = [
        {
            "document": "similar information",
            "source": "first.txt",
            "distance": 0.1,
            "metadata": {},
        },
        {
            "document": "similar information",
            "source": "second.txt",
            "distance": 0.1,
            "metadata": {},
        },
    ]

    result = resolve_with_ambiguity(
        "similar information",
        candidates,
    )

    assert result["ambiguous"] is True
    assert result["selected"] is None


def test_resolve_with_ambiguity_detects_low_confidence():
    candidates = [
        {
            "document": "",
            "source": "unknown.txt",
            "distance": 10.0,
            "metadata": {},
        },
    ]

    result = resolve_with_ambiguity(
        "something unrelated",
        candidates,
    )

    assert result["ambiguous"] is True
    assert result["selected"] is None