
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "app")

from retrieval.candidate_resolver import resolve_candidates

def test_resolve_candidates_ranks_heterogeneous_evidence():
    candidates = [
        {
            "document": "Project discussion and architecture notes",
            "source": "notes.txt",
            "distance": 0.4,
            "metadata": {"people": []},
        },
        {
            "document": "Unrelated content",
            "source": "other.pdf",
            "distance": 0.8,
            "metadata": {"people": []},
        },
    ]

    results = resolve_candidates(
        "project architecture",
        candidates,
        top_k=2,
    )

    assert len(results) == 2
    assert results[0]["source"] == "notes.txt"
    assert results[0]["resolution_score"] > results[1]["resolution_score"]

def test_resolve_candidates_empty():
    assert resolve_candidates("anything", []) == []


def test_resolve_candidates_handles_missing_evidence():
    candidates = [
        {
            "document": "",
            "source": None,
            "metadata": None,
        }
    ]

    results = resolve_candidates(
        "anything",
        candidates,
    )

    assert len(results) == 1
    assert "resolution_score" in results[0]


def test_resolve_candidates_respects_top_k():
    candidates = [
        {
            "document": f"document {index}",
            "source": f"file{index}.txt",
            "distance": float(index),
            "metadata": {},
        }
        for index in range(5)
    ]

    results = resolve_candidates(
        "document",
        candidates,
        top_k=2,
    )

    assert len(results) == 2