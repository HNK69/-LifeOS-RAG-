"""
test_batch4_persistence_lifecycle.py

Deterministic tests for Batch 4 persistence lifecycle & cleanup:
- BUG-P3-01: Non-destructive reindex and rollback protection on failure
- BUG-P3-02: Complete cleanup of derived state during reindex/reset
- BUG-P3-04: Complete file removal cleanup (Chroma, registry, face embeddings, orphaned anonymous identities)
- Idempotency and partial cleanup resilience
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

import config
from ingestion import ingest
from knowledge import document_registry as doc_reg
from knowledge import entity_relationship as entity_store
from knowledge import people as people_reg
from knowledge.reindex import reindex
from vectordb import chroma_db


@pytest.fixture(autouse=True)
def setup_test_databases(tmp_path, monkeypatch):
    """Isolate all persistence stores in a temporary directory for each test."""
    reg_db = tmp_path / "test_doc_reg.db"
    ent_db = tmp_path / "test_entities.db"
    peo_db = tmp_path / "test_people.db"
    docs_dir = tmp_path / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DOCUMENTS_DIR", docs_dir)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", docs_dir)
    monkeypatch.setattr(config, "REGISTRY_DB", reg_db)
    monkeypatch.setattr(doc_reg, "REGISTRY_DB", reg_db)
    monkeypatch.setattr(entity_store, "ENTITY_DB", ent_db)
    monkeypatch.setattr(people_reg, "PEOPLE_DB", peo_db)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    # Initialize SQLite schemas
    doc_reg.initialize_registry()
    entity_store.initialize_entity_store()
    people_reg.initialize_people_registry()

    # Mock LLM generation so tests are deterministic and offline
    monkeypatch.setattr("llm.generator.generate_response", lambda prompt: "{}")
    monkeypatch.setattr("knowledge.entity_extractor.generate_response", lambda prompt: "{}")
    monkeypatch.setattr(
        "knowledge.analyzer.analyze_document",
        lambda *args, **kwargs: {"entities": [], "relationships": [], "summary": "test"},
    )

    yield {
        "docs_dir": docs_dir,
        "reg_db": reg_db,
        "ent_db": ent_db,
        "peo_db": peo_db,
    }


# =====================================================================
# 1. BUG-P3-04 — INGESTION REMOVAL CLEANUP
# =====================================================================

def test_remove_file_cleans_chroma_and_document_registry(tmp_path):
    docs_dir = config.DOCUMENTS_DIR
    test_file = docs_dir / "report.txt"
    test_file.write_text("Quarterly financial report content.", encoding="utf-8")

    # Ingest document
    doc_reg.mark_success(test_file, "hash1", chunk_count=2)
    chroma_db.collection.upsert(
        ids=["chunk_1", "chunk_2"],
        documents=["chunk one", "chunk two"],
        embeddings=[[0.1] * 1024, [0.2] * 1024],
        metadatas=[
            {"file_path": str(test_file.resolve()), "chunk_id": 0},
            {"file_path": str(test_file.resolve()), "chunk_id": 1},
        ],
    )

    # Verify present before removal
    assert doc_reg.get_document(test_file) is not None
    assert len(chroma_db.collection.get(where={"file_path": str(test_file.resolve())})["ids"]) == 2

    # Remove document
    result = ingest.remove_file(test_file)
    assert result is True

    # Verify both Chroma and document registry are cleaned
    assert doc_reg.get_document(test_file) is None
    assert len(chroma_db.collection.get(where={"file_path": str(test_file.resolve())})["ids"]) == 0


def test_remove_file_cleans_face_embeddings_and_prunes_anonymous_person(tmp_path):
    image_file = config.DOCUMENTS_DIR / "photo1.jpg"
    image_file.write_bytes(b"dummy image bytes")

    # Register anonymous person with a face embedding from this image
    person_id = people_reg.create_person(label=None, is_user=False, status="unknown")
    embedding = np.array([0.5] * 128, dtype=np.float32)
    people_reg.add_face_embedding(person_id, embedding, source_path=image_file)

    # Also register media in document registry
    doc_reg.register_media(image_file, "imghash", {"media_type": "image"})
    chroma_db.store_media_description(
        image_file,
        "A photo of someone",
        np.array([0.1] * 1024, dtype=np.float32),
        people=[{"person_id": person_id, "label": None}],
    )

    # Verify present before removal
    assert people_reg.get_person(person_id) is not None
    scores = people_reg.score_face_against_person(embedding, person_id)
    assert len(scores) == 1

    # Remove file
    success = ingest.remove_file(image_file)
    assert success is True

    # Face embedding must be deleted
    scores_after = people_reg.score_face_against_person(embedding, person_id)
    assert len(scores_after) == 0

    # Since person was anonymous and now has 0 embeddings, they must be pruned
    assert people_reg.get_person(person_id) is None


def test_remove_file_preserves_confirmed_person_identity(tmp_path):
    image_file = config.DOCUMENTS_DIR / "alice.jpg"
    image_file.write_bytes(b"dummy image bytes")

    # Create a user-confirmed person
    person_id = people_reg.create_person(label="Alice", is_user=False, status="confirmed")
    embedding = np.array([0.3] * 128, dtype=np.float32)
    people_reg.add_face_embedding(person_id, embedding, source_path=image_file)

    # Remove file
    success = ingest.remove_file(image_file)
    assert success is True

    # Face embedding for that file is removed
    scores = people_reg.score_face_against_person(embedding, person_id)
    assert len(scores) == 0

    # Confirmed person identity is STRICTLY preserved
    person = people_reg.get_person(person_id)
    assert person is not None
    assert person["label"] == "Alice"
    assert person["status"] == "confirmed"


def test_remove_file_preserves_user_identity(tmp_path):
    image_file = config.DOCUMENTS_DIR / "user_selfie.jpg"
    image_file.write_bytes(b"dummy image bytes")

    # Create the user identity
    person_id = people_reg.create_user_identity("User Me")
    embedding = np.array([0.4] * 128, dtype=np.float32)
    people_reg.add_face_embedding(person_id, embedding, source_path=image_file)

    # Remove file
    success = ingest.remove_file(image_file)
    assert success is True

    # Confirmed user identity is STRICTLY preserved
    user_person = people_reg.get_person(person_id)
    assert user_person is not None
    assert user_person["is_user"] == 1


def test_remove_file_is_idempotent(tmp_path):
    test_file = config.DOCUMENTS_DIR / "temp.txt"
    test_file.write_text("temporary content", encoding="utf-8")

    doc_reg.mark_success(test_file, "hash_tmp", chunk_count=1)

    # First removal succeeds
    assert ingest.remove_file(test_file) is True
    # Second removal is idempotent and succeeds without exception
    assert ingest.remove_file(test_file) is True


def test_remove_file_partial_failure_resilience(tmp_path, monkeypatch):
    test_file = config.DOCUMENTS_DIR / "test.txt"
    test_file.write_text("content", encoding="utf-8")

    doc_reg.mark_success(test_file, "hash1", chunk_count=1)
    chroma_db.collection.upsert(
        ids=["chunk_1"],
        documents=["chunk"],
        embeddings=[[0.1] * 1024],
        metadatas=[{"file_path": str(test_file.resolve()), "chunk_id": 0}],
    )

    # Simulate Chroma delete raising an error
    def buggy_delete_doc(_):
        raise RuntimeError("Chroma connection dropped")

    monkeypatch.setattr("ingestion.ingest.delete_document", buggy_delete_doc)

    # remove_file should record the error, but preserve registry record for recovery
    success = ingest.remove_file(test_file)
    assert success is False

    # Document registry record is PRESERVED so that subsequent retry/reconciliation can clean Chroma
    rec = doc_reg.get_document(test_file)
    assert rec is not None
    assert rec["ingestion_status"] == "failed"
    assert "Removal failed: Chroma connection dropped" in rec["error_message"]


def test_remove_file_partial_failure_recovery_convergence(tmp_path, monkeypatch):
    """Verify that after Chroma failure, subsequent retry succeeds and converges to fully clean state."""
    test_file = config.DOCUMENTS_DIR / "converge.txt"
    test_file.write_text("content to be cleaned", encoding="utf-8")

    doc_reg.mark_success(test_file, "hash_conv", chunk_count=1)
    chroma_db.collection.upsert(
        ids=["chunk_conv_1"],
        documents=["chunk content"],
        embeddings=[[0.1] * 1024],
        metadatas=[{"file_path": str(test_file.resolve()), "chunk_id": 0}],
    )

    # 1. First attempt: Chroma fails
    fail_chroma = True

    def flaky_delete_doc(fpath):
        if fail_chroma:
            raise RuntimeError("Temporary Chroma error")
        return chroma_db.delete_document(fpath)

    monkeypatch.setattr("ingestion.ingest.delete_document", flaky_delete_doc)

    assert ingest.remove_file(test_file) is False
    # Registry record remains
    assert doc_reg.get_document(test_file) is not None
    assert len(chroma_db.collection.get(where={"file_path": str(test_file.resolve())})["ids"]) == 1

    # 2. Chroma recovers
    fail_chroma = False

    # Retry removal
    retry_success = ingest.remove_file(test_file)
    assert retry_success is True

    # 3. Everything converges to clean state
    assert doc_reg.get_document(test_file) is None
    assert len(chroma_db.collection.get(where={"file_path": str(test_file.resolve())})["ids"]) == 0


# =====================================================================
# 2. BUG-P3-01 & BUG-P3-02 — SAFE REINDEX AND ROLLBACK ON FAILURE
# =====================================================================

def test_reindex_successful_rebuild(tmp_path, monkeypatch):
    docs_dir = config.DOCUMENTS_DIR
    file1 = docs_dir / "doc1.txt"
    file1.write_text("Artificial Intelligence and Machine Learning overview.", encoding="utf-8")
    file2 = docs_dir / "doc2.txt"
    file2.write_text("Quantum Computing fundamentals and algorithms.", encoding="utf-8")

    # Mock embeddings and analysis for deterministic ingestion
    monkeypatch.setattr(ingest, "generate_embeddings", lambda chunks: np.array([[0.1] * 1024 for _ in chunks], dtype=np.float32))
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})

    # Initial indexing
    result = reindex(documents_dir=docs_dir)
    assert result["success"] is True

    # Both documents are in registry with status 'success'
    rec1 = doc_reg.get_document(file1)
    rec2 = doc_reg.get_document(file2)
    assert rec1 is not None and rec1["ingestion_status"] == "success"
    assert rec2 is not None and rec2["ingestion_status"] == "success"

    # Chunks are in Chroma
    chunks1 = chroma_db.collection.get(where={"file_path": str(file1.resolve())})["ids"]
    assert len(chunks1) > 0


def test_reindex_failure_rollback_preserves_valid_data(tmp_path, monkeypatch):
    docs_dir = config.DOCUMENTS_DIR
    file1 = docs_dir / "doc1.txt"
    file1.write_text("Original valid content for doc 1.", encoding="utf-8")

    monkeypatch.setattr(ingest, "generate_embeddings", lambda chunks: np.array([[0.2] * 1024 for _ in chunks], dtype=np.float32))
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})

    # Successfully index doc1 initially
    res1 = reindex(documents_dir=docs_dir)
    assert res1["success"] is True

    # Record baseline state
    rec_before = doc_reg.get_document(file1)
    assert rec_before["ingestion_status"] == "success"
    chroma_before = chroma_db.collection.get(where={"file_path": str(file1.resolve())})
    assert len(chroma_before["ids"]) > 0

    # Create a derived entity
    entity_store.create_entity("concept", "OriginalEntity")
    assert len(entity_store.find_entities(name="OriginalEntity")) == 1

    # Now add a second file that causes a simulated crash/error during reindex
    file2 = docs_dir / "doc2_corrupt.txt"
    file2.write_text("Corrupted file", encoding="utf-8")

    orig_ingest_file = ingest.ingest_file

    def failing_ingest_file(fpath, *args, **kwargs):
        if "doc2_corrupt" in str(fpath):
            raise RuntimeError("Catastrophic disk/network crash during doc2")
        return orig_ingest_file(fpath, *args, **kwargs)

    monkeypatch.setattr(ingest, "ingest_file", failing_ingest_file)

    # Run reindex with raise_on_error=True
    with pytest.raises(RuntimeError, match="Catastrophic disk/network crash"):
        reindex(documents_dir=docs_dir, raise_on_error=True)

    # VERIFY ROLLBACK:
    # 1. doc1's registry record is completely preserved as 'success'
    rec_after = doc_reg.get_document(file1)
    assert rec_after is not None
    assert rec_after["ingestion_status"] == "success"

    # 2. doc1's Chroma chunks were NOT destroyed
    chroma_after = chroma_db.collection.get(where={"file_path": str(file1.resolve())})
    assert len(chroma_after["ids"]) == len(chroma_before["ids"])

    # 3. Pre-existing entity was NOT permanently wiped
    entities_after = entity_store.find_entities(name="OriginalEntity")
    assert len(entities_after) == 1


def test_reindex_preserves_durable_person_linked_entities(tmp_path, monkeypatch):
    docs_dir = config.DOCUMENTS_DIR
    test_file = docs_dir / "notes.txt"
    test_file.write_text("Some notes", encoding="utf-8")

    monkeypatch.setattr(ingest, "generate_embeddings", lambda chunks: np.array([[0.1] * 1024 for _ in chunks], dtype=np.float32))
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})

    # Create confirmed person
    person_id = people_reg.create_person(label="Bob", is_user=False, status="confirmed")

    # Link person to an entity in entity store
    linked_entity = entity_store.link_person_to_entity(person_id, "Bob")
    assert linked_entity is not None

    # Also create a purely derived entity from document
    entity_store.create_entity("concept", "DerivedConcept")

    # Run reindex
    result = reindex(documents_dir=docs_dir, clean_derived_entities=True)
    assert result["success"] is True

    # Person-linked entity must be PRESERVED
    bob_entity = entity_store.get_entity_by_person_id(person_id)
    assert bob_entity is not None
    assert bob_entity["canonical_name"] == "Bob"

    # Purely derived unreferenced entity was cleaned up as part of clean rebuild
    assert len(entity_store.find_entities(name="DerivedConcept")) == 0


def test_reingest_media_prevents_duplicate_face_embeddings(tmp_path, monkeypatch):
    docs_dir = config.DOCUMENTS_DIR
    img_file = docs_dir / "profile.jpg"
    img_file.write_bytes(b"dummy image data")

    # Mock media processing
    monkeypatch.setattr(ingest, "analyze_image", lambda _: "A person's photo")
    monkeypatch.setattr(ingest, "analyze_image_metadata", lambda _: {})
    monkeypatch.setattr(ingest, "generate_embeddings", lambda _: np.array([[0.1] * 1024], dtype=np.float32))

    # Mock face extraction to return a stable embedding
    face_emb = np.array([0.7] * 128, dtype=np.float32)

    def mock_analyze_image_faces(path):
        identity = people_reg.identify_or_register_face(face_emb, source_path=path)
        return [{"status": identity["status"], "person_id": identity["person_id"], "similarity": 1.0}]

    monkeypatch.setattr(people_reg, "analyze_image_faces", mock_analyze_image_faces)

    # First ingestion
    ingest.ingest_file(img_file, force=True)

    with people_reg._connect() as conn:
        count1 = conn.execute(
            "SELECT COUNT(*) as count FROM face_embeddings WHERE source_path = ?",
            (str(img_file.resolve()),),
        ).fetchone()["count"]
    assert count1 == 1

    # Second ingestion with force=True (re-indexing the image)
    ingest.ingest_file(img_file, force=True)

    with people_reg._connect() as conn:
        count2 = conn.execute(
            "SELECT COUNT(*) as count FROM face_embeddings WHERE source_path = ?",
            (str(img_file.resolve()),),
        ).fetchone()["count"]

    # Must still be exactly 1 embedding, NOT 2 (prevented duplicate accumulation)
    assert count2 == 1


# =====================================================================
# 3. CONCURRENCY COORDINATION & REINDEX / MUTATION MUTUAL EXCLUSION
# =====================================================================

def test_concurrent_reindex_rejection(tmp_path):
    """Verify that a second concurrent reindex attempt is rejected."""
    from knowledge.coordination import acquire_reindex_lock

    docs_dir = config.DOCUMENTS_DIR
    lock_file = tmp_path / "reindex.lock"

    # Simulate another thread or process holding the reindex lock
    with acquire_reindex_lock(lock_path=lock_file):
        # Second reindex with raise_on_error=True must raise RuntimeError
        with pytest.raises(RuntimeError, match="Reindex already in progress"):
            reindex(documents_dir=docs_dir, raise_on_error=True, lock_path=lock_file)

        # Second reindex with raise_on_error=False must return success=False
        result = reindex(documents_dir=docs_dir, raise_on_error=False, lock_path=lock_file)
        assert result["success"] is False
        assert "Reindex already in progress" in result["error"]


def test_reindex_blocks_external_ingest_and_remove(tmp_path, monkeypatch):
    """Verify that external ingest_file and remove_file are rejected while reindex is active."""
    import threading
    from knowledge.coordination import acquire_reindex_lock

    lock_file = tmp_path / "reindex.lock"
    test_file = config.DOCUMENTS_DIR / "sample.txt"
    test_file.write_text("Hello world", encoding="utf-8")

    # In a separate thread, acquire the reindex lock
    acquired_event = threading.Event()
    release_event = threading.Event()

    def reindex_worker():
        with acquire_reindex_lock(lock_path=lock_file):
            acquired_event.set()
            release_event.wait(timeout=5.0)

    t = threading.Thread(target=reindex_worker)
    t.start()
    assert acquired_event.wait(timeout=2.0) is True

    try:
        # In this thread (not the reindexer), ingest_file and remove_file must be rejected
        ingest_res = ingest.ingest_file(test_file)
        assert ingest_res is False

        remove_res = ingest.remove_file(test_file)
        assert remove_res is False
    finally:
        release_event.set()
        t.join(timeout=2.0)


def test_reindex_defers_watcher_flush(tmp_path):
    """Verify that DocumentEventHandler defers flushing events while reindex is active."""
    import threading
    from knowledge.coordination import acquire_reindex_lock
    from knowledge.watcher import DocumentEventHandler

    handler = DocumentEventHandler(config.DOCUMENTS_DIR)
    test_path = config.DOCUMENTS_DIR / "new_doc.txt"
    handler._pending_events[str(test_path.resolve())] = "created"

    lock_file = tmp_path / "reindex.lock"

    # Run in a separate thread holding the reindex lock
    acquired_event = threading.Event()
    release_event = threading.Event()

    def reindex_holder():
        with acquire_reindex_lock(lock_path=lock_file):
            acquired_event.set()
            release_event.wait(timeout=5.0)

    t = threading.Thread(target=reindex_holder)
    t.start()
    assert acquired_event.wait(timeout=2.0) is True

    try:
        # Call _flush_events from this thread while reindex lock is held
        handler._flush_events()

        # Events must NOT have been cleared (deferred)
        assert len(handler._pending_events) == 1
        # Timer was re-armed
        assert handler._timer is not None
        handler._timer.cancel()
    finally:
        release_event.set()
        t.join(timeout=2.0)


def test_reindex_lock_released_on_success(tmp_path, monkeypatch):
    """Verify that reindex lock is released after successful rebuild."""
    from knowledge.coordination import is_reindexing_active

    docs_dir = config.DOCUMENTS_DIR
    f1 = docs_dir / "succ.txt"
    f1.write_text("success content", encoding="utf-8")
    monkeypatch.setattr(ingest, "generate_embeddings", lambda chunks: np.array([[0.1] * 1024 for _ in chunks], dtype=np.float32))
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})

    lock_file = tmp_path / "reindex.lock"
    result = reindex(documents_dir=docs_dir, lock_path=lock_file)
    assert result["success"] is True

    # Lock must be completely free
    assert is_reindexing_active(lock_path=lock_file) is False

    # Second reindex can run immediately
    res2 = reindex(documents_dir=docs_dir, lock_path=lock_file)
    assert res2["success"] is True


def test_reindex_lock_released_on_failure(tmp_path, monkeypatch):
    """Verify that reindex lock is released even if rebuild encounters a fatal error and rolls back."""
    from knowledge.coordination import is_reindexing_active

    docs_dir = config.DOCUMENTS_DIR
    lock_file = tmp_path / "reindex.lock"

    monkeypatch.setattr("knowledge.reindex.ingest_documents", Mock(side_effect=RuntimeError("Rebuild exploded!")))

    result = reindex(documents_dir=docs_dir, raise_on_error=False, lock_path=lock_file)
    assert result["success"] is False

    # Lock must be released despite failure
    assert is_reindexing_active(lock_path=lock_file) is False


def test_reindex_stale_lock_recovery_after_process_termination(tmp_path):
    """Verify that an OS-level file lock held by a killed process does not permanently disable reindexing."""
    import subprocess
    import sys
    import time
    from filelock import FileLock, Timeout

    lock_file = tmp_path / "stale_test.lock"

    # Start a child process that acquires the lock and sleeps
    child_code = f"""
from filelock import FileLock
import time
l = FileLock(r"{lock_file}")
l.acquire()
time.sleep(100)
"""
    p = subprocess.Popen([sys.executable, "-c", child_code])
    time.sleep(1.0)

    # Verify lock is held by child
    probe = FileLock(str(lock_file))
    try:
        probe.acquire(timeout=0.1)
        pytest.fail("Should not acquire while child holds lock")
    except Timeout:
        pass

    # Abruptly kill the child process (simulating a crash / power cut)
    p.kill()
    p.wait()
    time.sleep(0.5)

    # The OS must automatically free the lock; reindex or lock acquisition must succeed
    probe.acquire(timeout=1.0)
    probe.release()

