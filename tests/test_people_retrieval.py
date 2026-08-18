import sys

sys.path.insert(0, ".")
sys.path.insert(0, "app")

import chromadb
import pytest

from app.retrieval import people_retriever


@pytest.fixture
def isolated_collection(monkeypatch):
    client = chromadb.EphemeralClient()
    collection = client.get_or_create_collection(
        name="test_people_retrieval"
    )

    monkeypatch.setattr(
        people_retriever,
        "collection",
        collection,
    )

    return collection


def store_image(collection, image_id, filename, people):
    collection.upsert(
        ids=[image_id],
        documents=[f"description for {filename}"],
        embeddings=[[0.1, 0.2, 0.3]],
        metadatas=[{
            "document_type": "media",
            "source": filename,
            "file_path": f"/test/{filename}",
            "people": __import__("json").dumps(people),
        }],
    )


def test_retrieve_by_person_matches_label_case_insensitively(
    isolated_collection,
):
    store_image(
        isolated_collection,
        "image-1",
        "photo1.jpg",
        [
            {
                "person_id": "person-1",
                "label": "Rahul",
                "similarity": 0.91,
            }
        ],
    )

    results = people_retriever.retrieve_by_person("rahul")

    assert len(results) == 1
    assert results[0]["person_id"] == "person-1"
    assert results[0]["label"] == "Rahul"


def test_retrieve_by_people_requires_all_requested_people(
    isolated_collection,
):
    store_image(
        isolated_collection,
        "image-1",
        "both.jpg",
        [
            {"person_id": "p1", "label": "Rahul"},
            {"person_id": "p2", "label": "Arun"},
        ],
    )

    store_image(
        isolated_collection,
        "image-2",
        "rahul.jpg",
        [
            {"person_id": "p1", "label": "Rahul"},
        ],
    )

    results = people_retriever.retrieve_by_people(
        ["Rahul", "Arun"]
    )

    assert len(results) == 1
    assert results[0]["file_path"] == "/test/both.jpg"


def test_retrieve_by_people_empty_labels_returns_no_results(
    isolated_collection,
):
    assert people_retriever.retrieve_by_people([]) == []