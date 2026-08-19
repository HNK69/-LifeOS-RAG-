import sys
sys.path.insert(0, ".")
sys.path.insert(0, "app")

import numpy as np

from vectordb import chroma_db


def test_store_media_description_persists_visual_metadata(
    monkeypatch,
    tmp_path,
):
    image_path = tmp_path / "photo.jpg"

    captured = {}

    class FakeCollection:
        def upsert(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        chroma_db,
        "collection",
        FakeCollection(),
    )

    chroma_db.store_media_description(
        image_path,
        "A photo in a classroom.",
        np.array([0.1, 0.2, 0.3]),
        people=[],
        visual_metadata={
            "objects": ["laptop"],
            "locations": ["classroom"],
            "activities": ["studying"],
            "context": "Academic setting.",
            "ocr": "DBMS",
            "entities": ["laptop"],
        },
    )

    metadata = captured["metadatas"][0]

    assert "visual_metadata" in metadata
    assert "laptop" in metadata["visual_metadata"]
    assert "classroom" in metadata["visual_metadata"]