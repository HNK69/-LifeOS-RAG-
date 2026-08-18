import sys

sys.path.insert(0, ".")
sys.path.insert(0, "app")

import numpy as np
import pytest

from app.knowledge import people


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        people,
        "PEOPLE_DB",
        tmp_path / "people_registry.db",
    )
    people.initialize_people_registry()


def test_existing_embedding_matches_same_person(isolated_registry):
    embedding = np.ones(512, dtype=np.float32)

    person_id = people.create_person()
    people.add_face_embedding(person_id, embedding)

    result = people.identify_or_register_face(embedding)

    assert result["person_id"] == person_id
    assert result["status"] == "matched"
    assert result["created"] is False


def test_new_embedding_creates_new_person(isolated_registry):
    known = np.ones(512, dtype=np.float32)

    different = np.zeros(512, dtype=np.float32)
    different[0] = 1.0

    person_id = people.create_person()
    people.add_face_embedding(person_id, known)

    result = people.identify_or_register_face(different)

    assert result["person_id"] != person_id
    assert result["status"] == "unknown"
    assert result["created"] is True


def test_confirm_person_identity_persists_label(isolated_registry):
    person_id = people.create_person()

    assert people.confirm_person_identity(
        person_id,
        "Test Person",
    ) is True

    person = people.get_person(person_id)

    assert person["label"] == "Test Person"
    assert person["status"] == "confirmed"
    assert person["is_user"] == 0

def test_get_people_metadata_uses_recognized_people(monkeypatch):
    monkeypatch.setattr(
        people,
        "analyze_image_faces",
        lambda _: [
            {
                "person_id": "person-1",
                "similarity": 0.95,
                "status": "matched",
            },
            {
                "person_id": None,
                "similarity": None,
                "status": "unknown",
            },
        ],
    )

    monkeypatch.setattr(
        people,
        "get_person",
        lambda person_id: {
            "person_id": person_id,
            "label": "Rahul",
        },
    )

    result = people.get_people_metadata("photo.jpg")

    assert result == {
        "people": [
            {
                "person_id": "person-1",
                "label": "Rahul",
                "similarity": 0.95,
            }
        ]
    }