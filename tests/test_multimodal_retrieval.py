import sys
sys.path.insert(0, ".")
sys.path.insert(0, "app")

import numpy as np

from retrieval.multimodal_retriever import retrieve_multimodal,search_multimodal


def test_retrieve_multimodal_returns_ranked_candidates(monkeypatch):
    class FakeCollection:
        def query(self, **kwargs):
            assert kwargs["n_results"] == 2
            assert kwargs["include"] == [
                "documents",
                "metadatas",
                "distances",
            ]

            return {
                "documents": [["Rahul at college", "DBMS project report"]],
                "metadatas": [[
                    {
                        "source": "photo.jpg",
                        "file_path": "/photos/photo.jpg",
                        "document_type": "media",
                        "media_type": "image",
                        "people": '[{"label": "Rahul"}]',
                    },
                    {
                        "source": "report.pdf",
                        "file_path": "/docs/report.pdf",
                        "document_type": "document",
                        "media_type": None,
                        "people": None,
                    },
                ]],
                "distances": [[0.1, 0.4]],
            }

    monkeypatch.setattr(
        "retrieval.multimodal_retriever.collection",
        FakeCollection(),
    )

    embedding = np.array(
        [0.1, 0.2, 0.3],
        dtype=np.float32,
    )

    results = retrieve_multimodal(
        embedding,
        top_k=2,
    )

    assert len(results) == 2

    assert results[0]["source"] == "photo.jpg"
    assert results[0]["document_type"] == "media"
    assert results[0]["media_type"] == "image"

    assert results[1]["source"] == "report.pdf"
    assert results[1]["document_type"] == "document"

    assert results[0]["distance"] < results[1]["distance"]

def test_retrieve_multimodal_none_embedding():
    assert retrieve_multimodal(None) == []


def test_retrieve_multimodal_empty_results(monkeypatch):
    class EmptyCollection:
        def query(self, **kwargs):
            return {
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

    monkeypatch.setattr(
        "retrieval.multimodal_retriever.collection",
        EmptyCollection(),
    )

    import numpy as np

    embedding = np.array(
        [0.1, 0.2, 0.3],
        dtype=np.float32,
    )

    assert retrieve_multimodal(embedding) == []


def test_search_multimodal_generates_query_embedding(monkeypatch):
    import numpy as np

    monkeypatch.setattr(
        "retrieval.multimodal_retriever.generate_embeddings",
        lambda queries: np.array([[0.1, 0.2, 0.3]], dtype=np.float32),
    )

    monkeypatch.setattr(
        "retrieval.multimodal_retriever.retrieve_multimodal",
        lambda embedding, top_k: [
            {"source": "photo.jpg", "distance": 0.1}
        ],
    )

    result = search_multimodal("photos from college", top_k=5)

    assert result["ambiguous"] is False
    assert result["selected"]["source"] == "photo.jpg"
    assert result["candidates"][0]["source"] == "photo.jpg"
    assert result["candidates"][0]["resolution_score"] > 0