import sys

sys.path.insert(0, ".")
sys.path.insert(0, "app")

from pathlib import Path

from app.ingestion import ingest


def test_image_ingestion_stores_people_metadata(
    tmp_path,
    monkeypatch,
):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake-image")

    monkeypatch.setattr(
        ingest,
        "is_media_file",
        lambda _: True,
    )

    monkeypatch.setattr(
        ingest,
        "get_media_metadata",
        lambda _: {
            "media_type": "image",
            "filename": "photo.jpg",
            "extension": ".jpg",
            "path": str(image_path),
            "file_size": image_path.stat().st_size,
            "modified_time": image_path.stat().st_mtime,
            "modified_time_ns": image_path.stat().st_mtime_ns,
        },
    )

    monkeypatch.setattr(
        ingest,
        "calculate_file_hash",
        lambda _: "test-hash",
    )

    monkeypatch.setattr(
        ingest,
        "file_needs_processing",
        lambda _: True,
    )

    monkeypatch.setattr(
        ingest,
        "is_unchanged",
        lambda *_: False,
    )

    monkeypatch.setattr(
        ingest,
        "analyze_image",
        lambda _: "A photo containing Rahul.",
    )

    monkeypatch.setattr(
        ingest,
        "analyze_image_metadata",
        lambda _: {
            "objects": ["laptop"],
            "locations": ["classroom"],
            "activities": ["studying"],
            "context": "A classroom study scene.",
            "ocr": "DBMS",
            "entities": ["laptop"],
        },
    )

    monkeypatch.setattr(
        ingest,
        "generate_embeddings",
        lambda _: [[0.1, 0.2, 0.3]],
    )

    monkeypatch.setattr(
        ingest,
        "get_people_metadata",
        lambda _: {
            "people": [
                {
                    "person_id": "person-1",
                    "label": "Rahul",
                    "similarity": 0.95,
                }
            ]
        },
    )

    stored = {}

    def fake_store_media_description(
        file_path,
        description,
        embedding,
        people=None,
        visual_metadata=None,
    ):
        stored["file_path"] = str(file_path)
        stored["description"] = description
        stored["embedding"] = embedding
        stored["people"] = people
        stored["visual_metadata"] = visual_metadata

    monkeypatch.setattr(
        ingest,
        "store_media_description",
        fake_store_media_description,
    )

    registered = {}

    def fake_register_media(
        file_path,
        file_hash,
        media_metadata,
    ):
        registered["file_path"] = str(file_path)
        registered["file_hash"] = file_hash
        registered["metadata"] = media_metadata

    monkeypatch.setattr(
        ingest,
        "register_media",
        fake_register_media,
    )

    monkeypatch.setattr(
        ingest,
        "initialize_registry",
        lambda: None,
    )

    assert ingest.ingest_file(image_path) is True

    assert stored["description"] == "A photo containing Rahul."
    assert stored["people"][0]["person_id"] == "person-1"
    assert stored["people"][0]["label"] == "Rahul"

    assert registered["file_hash"] == "test-hash"

    assert registered["metadata"]["visual_metadata"]["objects"] == ["laptop"]
    assert registered["metadata"]["visual_metadata"]["locations"] == ["classroom"]