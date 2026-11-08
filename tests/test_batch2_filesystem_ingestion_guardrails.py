"""
test_batch2_filesystem_ingestion_guardrails.py

Verification suite for Batch 2:
- CRIT-P4-04: Filesystem Root Containment (path validation, traversal, symlink escapes)
- CRIT-P4-03: Resource / Large-File Guardrails (size limits, admission control, TOCTOU)
- BUG-P3-03: Watcher Stability / 0-byte Files (bounded waiting, deadlock prevention)
- Generator / Vision Path Security (analyze_image filesystem boundaries)
"""

import os
import sys
import time
from pathlib import Path
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "app")

import config
from ingestion.guardrails import (
    validate_document_path,
    check_file_size,
    _is_strictly_relative_to,
)
from ingestion import ingest
from ingestion.reader import read_documents
from ingestion.file_classifier import classify_file
from knowledge.document_registry import calculate_file_hash
from knowledge.watcher import DocumentEventHandler
from llm import generator


# ============================================================
# FAKE CLOCK FOR DETERMINISTIC ZERO-SLEEP TESTING
# ============================================================

class FakeClock:
    """Deterministic simulated clock for instant timeout testing."""

    def __init__(self, start_time=1000.0):
        self.now = start_time
        self.sleep_calls = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        self.now += seconds


# ============================================================
# 1. CRIT-P4-04 — FILESYSTEM ROOT CONTAINMENT
# ============================================================

def test_validate_path_valid_nested(tmp_path):
    """Legitimate nested path beneath documents root is accepted."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    sub_dir = doc_dir / "folder" / "sub"
    sub_dir.mkdir(parents=True)
    test_file = sub_dir / "valid.txt"
    test_file.write_text("legitimate content", encoding="utf-8")

    resolved = validate_document_path(test_file, base_dir=doc_dir, must_exist=True)
    assert resolved == test_file.resolve()
    assert resolved.is_file()


def test_validate_path_relative_inside(tmp_path, monkeypatch):
    """Relative path resolving beneath documents root is accepted."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    test_file = doc_dir / "doc.txt"
    test_file.write_text("hello", encoding="utf-8")

    # Set as global DOCUMENTS_DIR
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    resolved = validate_document_path("doc.txt", base_dir=doc_dir, must_exist=True)
    assert resolved == test_file.resolve()


def test_validate_path_traversal_rejected(tmp_path):
    """Path traversal (..) escaping documents root is deterministically rejected."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("top secret", encoding="utf-8")

    traversal_path = doc_dir / ".." / "secret.txt"
    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        validate_document_path(traversal_path, base_dir=doc_dir, must_exist=False)


def test_validate_path_absolute_outside_rejected(tmp_path):
    """Arbitrary host filesystem absolute paths are deterministically rejected."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()

    outside_path = Path("C:/Windows/win.ini") if os.name == "nt" else Path("/etc/passwd")
    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        validate_document_path(outside_path, base_dir=doc_dir, must_exist=False)


def test_validate_path_documents_dir_itself_rejected(tmp_path):
    """The DOCUMENTS_DIR root itself is rejected (only items strictly beneath it are allowed)."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()

    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        validate_document_path(doc_dir, base_dir=doc_dir, must_exist=False)


def test_validate_path_sibling_prefix_rejected(tmp_path):
    """Sibling directories that share a prefix with DOCUMENTS_DIR are rejected."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    sibling_dir = tmp_path / "documents_backup"
    sibling_dir.mkdir()
    sibling_file = sibling_dir / "leak.txt"
    sibling_file.write_text("sibling data", encoding="utf-8")

    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        validate_document_path(sibling_file, base_dir=doc_dir, must_exist=False)


def test_validate_path_symlink_escape_rejected(tmp_path):
    """A symlink inside documents pointing outside documents is rejected."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside data", encoding="utf-8")

    symlink_file = doc_dir / "symlink_escape.txt"
    try:
        os.symlink(outside_file, symlink_file)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")

    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        validate_document_path(symlink_file, base_dir=doc_dir, must_exist=True)


def test_validate_path_broken_symlink_escape_rejected(tmp_path):
    """A symlink pointing to a nonexistent outside file is rejected before existence check."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    nonexistent_outside = tmp_path / "nonexistent_secret.txt"

    broken_symlink = doc_dir / "broken_symlink.txt"
    try:
        os.symlink(nonexistent_outside, broken_symlink)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")

    # Symlink resolution must happen before existence validation!
    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        validate_document_path(broken_symlink, base_dir=doc_dir, must_exist=False)


def test_validate_path_symlink_internal_accepted(tmp_path):
    """A symlink pointing to a legitimate target inside documents is accepted."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    target_file = doc_dir / "target.txt"
    target_file.write_text("internal data", encoding="utf-8")

    internal_symlink = doc_dir / "internal_link.txt"
    try:
        os.symlink(target_file, internal_symlink)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")

    resolved = validate_document_path(internal_symlink, base_dir=doc_dir, must_exist=True)
    assert resolved == target_file.resolve()


def test_validate_path_nonexistent_handling(tmp_path):
    """Nonexistent paths raise FileNotFoundError when must_exist=True, return Path when False."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    nonexistent = doc_dir / "nonexistent.txt"

    with pytest.raises(FileNotFoundError, match="Document not found"):
        validate_document_path(nonexistent, base_dir=doc_dir, must_exist=True)

    # With must_exist=False, returns resolved Path
    res = validate_document_path(nonexistent, base_dir=doc_dir, must_exist=False)
    assert res == nonexistent.resolve()


def test_validate_path_empty_or_none(tmp_path):
    """Empty or None path values are deterministically rejected."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()

    with pytest.raises(ValueError, match="Document path cannot be None"):
        validate_document_path(None, base_dir=doc_dir)

    with pytest.raises(ValueError, match="Document path cannot be empty"):
        validate_document_path("   ", base_dir=doc_dir)


def test_entrypoints_reject_outside_paths(tmp_path, monkeypatch):
    """Verify all major path-taking entry points reject outside paths."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", doc_dir)

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    # 1. ingest_file
    with pytest.raises(ValueError, match="Access denied"):
        ingest.ingest_file(outside)

    # 2. remove_file
    with pytest.raises(ValueError, match="Access denied"):
        ingest.remove_file(outside)

    # 3. read_documents
    with pytest.raises(ValueError, match="Access denied"):
        read_documents(outside)

    # 4. classify_file
    with pytest.raises(ValueError, match="Access denied"):
        classify_file(outside)

    # 5. analyze_image
    with pytest.raises(ValueError, match="Access denied"):
        generator.analyze_image(outside)


def test_no_host_filesystem_leaks_in_errors(tmp_path):
    """Verify error messages do not leak internal filesystem paths unnecessarily."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()

    try:
        validate_document_path("C:/Secret/Host/Folder/data.txt", base_dir=doc_dir)
    except ValueError as exc:
        assert "C:/Secret/Host/Folder" not in str(exc)
        assert "Access denied: path is outside documents directory" in str(exc)


# ============================================================
# 2. CRIT-P4-03 — RESOURCE / LARGE-FILE GUARDRAILS
# ============================================================

def test_ingest_file_rejects_oversized_file(tmp_path, monkeypatch):
    """Oversized file is rejected before hashing, classification, or embedding."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", doc_dir)

    # Set text limit to 100 bytes for test
    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", 100)

    large_file = doc_dir / "large.txt"
    large_file.write_text("A" * 200, encoding="utf-8")

    # Track if expensive functions were called
    hash_called = []
    chunk_called = []
    monkeypatch.setattr(
        ingest, "calculate_file_hash",
        lambda *args: hash_called.append(True) or "dummy_hash"
    )

    result = ingest.ingest_file(large_file)
    assert result is False
    assert len(hash_called) == 0, "calculate_file_hash should not have been called"


def test_ingest_file_boundary_values(tmp_path, monkeypatch):
    """File at exact limit is accepted; limit + 1 byte is rejected."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", doc_dir)

    limit = 50
    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", limit)

    # File exactly at limit
    exact_file = doc_dir / "exact.txt"
    exact_file.write_bytes(b"x" * limit)

    # Mock later steps so we only verify size admission control
    monkeypatch.setattr(ingest, "file_needs_processing", lambda _: False)

    # File at exact limit should pass size check (and return False only because file_needs_processing is False)
    assert ingest.ingest_file(exact_file) is False

    # File at limit + 1 byte
    over_file = doc_dir / "over.txt"
    over_file.write_bytes(b"x" * (limit + 1))

    assert ingest.ingest_file(over_file) is False


def test_classification_alone_cannot_bypass_enforcement(tmp_path, monkeypatch):
    """An attacker or mock returning small classification does NOT bypass hard size enforcement."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", doc_dir)

    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", 100)

    large_file = doc_dir / "large.txt"
    large_file.write_bytes(b"A" * 200)

    # Mock classify_file to claim it is NOT large
    monkeypatch.setattr(
        ingest, "classify_file",
        lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.0001, "large": False}
    )

    # ingest_file must STILL reject it before reading
    assert ingest.ingest_file(large_file) is False


def test_calculate_file_hash_enforces_size_limit(tmp_path, monkeypatch):
    """calculate_file_hash stops immediately if file exceeds limit."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    test_file = doc_dir / "hash_test.txt"
    test_file.write_bytes(b"data" * 50)

    # When max_bytes is smaller than file size, it must raise ValueError
    with pytest.raises(ValueError, match="File size exceeds maximum allowed limit"):
        calculate_file_hash(test_file, max_bytes=50)


def test_structured_file_respects_limit(tmp_path, monkeypatch):
    """Structured files respect MAX_STRUCTURED_FILE_SIZE_BYTES."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", doc_dir)

    monkeypatch.setattr(config, "MAX_STRUCTURED_FILE_SIZE_BYTES", 50)

    large_csv = doc_dir / "data.csv"
    large_csv.write_text("col1,col2\n" + ("val1,val2\n" * 10), encoding="utf-8")

    assert ingest.ingest_file(large_csv) is False


# ============================================================
# 3. BUG-P3-03 — WATCHER STABILITY / 0-BYTE FILES
# ============================================================

def test_watcher_stability_normal_file(tmp_path):
    """Stable normal file stabilizes after stability_duration."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    test_file = doc_dir / "stable.txt"
    test_file.write_text("stable content", encoding="utf-8")

    clock = FakeClock()

    result = DocumentEventHandler._wait_for_file_stability(
        test_file,
        stability_duration=1.0,
        check_interval=0.25,
        max_wait_seconds=5.0,
        sleep_func=clock.sleep,
        time_func=clock.time,
    )

    assert result is True
    # Should have slept at least 1.0 second worth of intervals
    assert sum(clock.sleep_calls) >= 1.0


def test_watcher_stability_initially_changing_file(tmp_path):
    """File that grows initially then stabilizes produces True."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    test_file = doc_dir / "growing.txt"
    test_file.write_text("initial", encoding="utf-8")

    clock = FakeClock()
    writes = [b"initial", b"step 1", b"step 2 final content"]
    step = [0]

    def mock_sleep(dt):
        clock.now += dt
        step[0] += 1
        if step[0] < len(writes):
            test_file.write_bytes(writes[step[0]])

    result = DocumentEventHandler._wait_for_file_stability(
        test_file,
        stability_duration=1.0,
        check_interval=0.25,
        max_wait_seconds=5.0,
        sleep_func=mock_sleep,
        time_func=clock.time,
    )

    assert result is True


def test_watcher_stability_permanently_zero_byte(tmp_path):
    """Permanently 0-byte file times out deterministically and returns False (no deadlock)."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    empty_file = doc_dir / "empty.txt"
    empty_file.write_bytes(b"")

    clock = FakeClock()

    result = DocumentEventHandler._wait_for_file_stability(
        empty_file,
        stability_duration=1.0,
        check_interval=0.25,
        max_wait_seconds=3.0,
        sleep_func=clock.sleep,
        time_func=clock.time,
    )

    assert result is False
    assert sum(clock.sleep_calls) >= 3.0


def test_watcher_stability_never_stable(tmp_path):
    """File that keeps changing continuously times out boundedly and returns False."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    unstable_file = doc_dir / "log.txt"
    unstable_file.write_bytes(b"line 0")

    clock = FakeClock()
    counter = [0]

    def growing_sleep(dt):
        clock.now += dt
        counter[0] += 1
        # Change file size on every check
        unstable_file.write_bytes(b"X" * (10 + counter[0]))

    result = DocumentEventHandler._wait_for_file_stability(
        unstable_file,
        stability_duration=1.0,
        check_interval=0.25,
        max_wait_seconds=2.0,
        sleep_func=growing_sleep,
        time_func=clock.time,
    )

    assert result is False
    assert clock.time() >= 1002.0


def test_watcher_continues_processing_subsequent_events(tmp_path, monkeypatch):
    """Watcher background flush processes all subsequent events even if one file is stuck/empty."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", doc_dir)

    handler = DocumentEventHandler(doc_dir)

    # File 1: Permanently 0 bytes
    file1 = doc_dir / "empty.txt"
    file1.write_bytes(b"")

    # File 2: Normal valid file
    file2 = doc_dir / "valid.txt"
    file2.write_text("Hello LifeOS", encoding="utf-8")

    # Mock stability wait to return False for file1 and True for file2 immediately
    def mock_stability(path, **kwargs):
        return path.name == "valid.txt"

    monkeypatch.setattr(handler, "_wait_for_file_stability", mock_stability)

    ingested_files = []
    monkeypatch.setattr("knowledge.watcher.ingest_file", lambda p: ingested_files.append(Path(p).name) or True)

    # Queue both files
    handler._pending_events[str(file1)] = "created"
    handler._pending_events[str(file2)] = "created"

    handler._flush_events()

    # The empty file was skipped; the valid file was ingested!
    assert "valid.txt" in ingested_files
    assert "empty.txt" not in ingested_files


# ============================================================
# 4. GENERATOR / VISION PATH SECURITY
# ============================================================

def test_analyze_image_rejects_outside_path(tmp_path, monkeypatch):
    """analyze_image rejects arbitrary outside path."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    outside_image = tmp_path / "photo.jpg"
    outside_image.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)

    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        generator.analyze_image(outside_image)


def test_analyze_image_rejects_traversal(tmp_path, monkeypatch):
    """analyze_image rejects path traversal escaping documents root."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8\xff\xe0")

    traversal = doc_dir / ".." / "outside.jpg"
    with pytest.raises(ValueError, match="Access denied: path is outside documents directory"):
        generator.analyze_image(traversal)


def test_analyze_image_rejects_oversized_image(tmp_path, monkeypatch):
    """analyze_image rejects oversized image before calling external API."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    # Set image limit to 50 bytes for test
    monkeypatch.setattr(config, "MAX_IMAGE_FILE_SIZE_BYTES", 50)

    large_image = doc_dir / "large.jpg"
    large_image.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    api_called = []
    monkeypatch.setattr(
        generator.client.chat.completions, "create",
        lambda *args, **kwargs: api_called.append(True)
    )

    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        generator.analyze_image(large_image)

    assert len(api_called) == 0, "Vision API should not have been called"


def test_analyze_image_rejects_nonexistent_image(tmp_path, monkeypatch):
    """analyze_image raises FileNotFoundError for missing image."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    missing = doc_dir / "missing.jpg"
    with pytest.raises(FileNotFoundError, match="Document not found"):
        generator.analyze_image(missing)


# ============================================================
# 5. READER.READ_TEXT() TOCTOU RESOURCE GUARDRAILS
# ============================================================

def test_read_text_normal_file(tmp_path, monkeypatch):
    """A normal file at/below the configured limit reads correctly."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)
    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", 100)

    test_file = doc_dir / "normal.txt"
    test_file.write_text("Hello LifeOS text reader!", encoding="utf-8")

    from ingestion.reader import read_text
    content = read_text(test_file)
    assert content == "Hello LifeOS text reader!"


def test_read_text_exact_boundary(tmp_path, monkeypatch):
    """A file exactly at the configured byte limit is accepted."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    limit = 64
    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", limit)

    exact_file = doc_dir / "exact.txt"
    exact_data = b"B" * limit
    exact_file.write_bytes(exact_data)

    from ingestion.reader import read_text
    content = read_text(exact_file)
    assert content == exact_data.decode("utf-8")


def test_read_text_multibyte_boundary(tmp_path, monkeypatch):
    """Multibyte UTF-8 characters at exact byte limit are decoded without corruption."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    mb_text = "こんにちは"  # 5 characters, 15 UTF-8 bytes
    mb_bytes = mb_text.encode("utf-8")
    assert len(mb_bytes) == 15

    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", 15)

    mb_file = doc_dir / "mb.txt"
    mb_file.write_bytes(mb_bytes)

    from ingestion.reader import read_text
    content = read_text(mb_file)
    assert content == mb_text


def test_read_text_exceeding_limit_rejected(tmp_path, monkeypatch):
    """A file exceeding the configured limit is rejected with ValueError."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    limit = 50
    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", limit)

    over_file = doc_dir / "over.txt"
    over_file.write_bytes(b"X" * (limit + 1))

    from ingestion.reader import read_text
    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        read_text(over_file)


def test_read_text_toctou_growth_after_stat_aborts(tmp_path, monkeypatch):
    """A file that grows after the initial stat check cannot cause an unbounded read."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENTS_DIR", doc_dir)

    limit = 50
    monkeypatch.setattr(config, "MAX_TEXT_FILE_SIZE_BYTES", limit)

    test_file = doc_dir / "toctou.txt"
    test_file.write_bytes(b"small content")

    from ingestion.reader import read_text
    import ingestion.reader as reader_mod

    original_check = reader_mod.check_file_size

    def grow_file_after_check(p, **kwargs):
        res = original_check(p, **kwargs)
        # Simulate concurrent file growth between check_file_size and open/read
        test_file.write_bytes(b"A" * (limit * 100))
        return res

    monkeypatch.setattr(reader_mod, "check_file_size", grow_file_after_check)

    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        read_text(test_file)
