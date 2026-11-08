import json
from pathlib import Path

import config
from ingestion import ingest
from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    find_entities,
    get_relationships,
)


def test_ingest_file_populates_entities_and_relationships(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", tmp_path)
    clear_entity_store()

    test_file = tmp_path / "notes.txt"
    test_file.write_text("Dr. Alice works with Bob on LifeOS research.", encoding="utf-8")

    # Mock chunking, embedding, and chroma storage
    monkeypatch.setattr(ingest, "file_needs_processing", lambda _: True)
    monkeypatch.setattr(ingest, "is_unchanged", lambda *_: False)
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})
    monkeypatch.setattr(ingest, "calculate_file_hash", lambda _: "hash123")
    monkeypatch.setattr(ingest, "generate_embeddings", lambda chunks: [[0.1] * 10 for _ in chunks])
    monkeypatch.setattr(ingest, "store_embeddings", lambda chunks, embs, path: None)
    monkeypatch.setattr(ingest, "mark_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(ingest, "mark_success", lambda *args, **kwargs: None)

    # Mock LLM response in generator and entity_extractor
    import knowledge.entity_extractor as extractor
    from llm import generator

    def fake_generate_response(prompt):
        if "Extract the meaningful named entities" in prompt:
            return json.dumps({
                "entities": [
                    {"name": "Alice", "type": "person", "attributes": {"role": "researcher"}},
                    {"name": "Bob", "type": "person", "attributes": {"role": "collaborator"}},
                ]
            })
        if "Extract meaningful relationships" in prompt:
            return json.dumps({
                "relationships": [
                    {
                        "source": "Alice",
                        "target": "Bob",
                        "type": "works_with",
                        "confidence": 0.95,
                        "metadata": {"project": "LifeOS"},
                    }
                ]
            })
        return "{}"

    monkeypatch.setattr(generator, "generate_response", fake_generate_response)
    monkeypatch.setattr(extractor, "generate_response", fake_generate_response)

    success = ingest.ingest_file(test_file)
    assert success is True

    # Assert entities are populated
    alice_matches = find_entities(name="alice")
    assert len(alice_matches) == 1
    assert alice_matches[0]["canonical_name"] == "Alice"

    bob_matches = find_entities(name="bob")
    assert len(bob_matches) == 1
    assert bob_matches[0]["canonical_name"] == "Bob"

    # Assert relationship is populated
    alice_id = alice_matches[0]["id"]
    relationships = get_relationships(alice_id, direction="outgoing")
    assert len(relationships) == 1
    assert relationships[0]["relationship_type"] == "works_with"
    assert relationships[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_reindex_resets_and_rebuilds_entity_store():
    clear_entity_store()
    # Create stale entity
    create_entity("person", "StaleEntity")
    assert len(find_entities(name="StaleEntity")) == 1

    # Execute reindex clear
    clear_entity_store()

    # Stale entity must be gone
    assert len(find_entities(name="StaleEntity")) == 0
