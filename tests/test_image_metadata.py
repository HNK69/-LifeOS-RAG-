import sys

sys.path.insert(0, ".")
sys.path.insert(0, "app")

from llm.generator import analyze_image_metadata


def test_analyze_image_metadata_returns_structured_metadata(monkeypatch):
    monkeypatch.setattr(
        "llm.generator.analyze_image",
        lambda file_path, prompt=None: """
        {
            "objects": ["laptop", "notebook"],
            "locations": ["classroom"],
            "activities": ["studying"],
            "context": "A student studying in a classroom.",
            "ocr": "DBMS",
            "entities": ["laptop"]
        }
        """,
    )

    result = analyze_image_metadata("photo.jpg")

    assert result["objects"] == ["laptop", "notebook"]
    assert result["locations"] == ["classroom"]
    assert result["activities"] == ["studying"]
    assert result["context"] == "A student studying in a classroom."
    assert result["ocr"] == "DBMS"
    assert result["entities"] == ["laptop"]