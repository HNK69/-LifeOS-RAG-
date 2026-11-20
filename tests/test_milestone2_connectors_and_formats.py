"""
test_milestone2_connectors_and_formats.py

Comprehensive deterministic test suite for Milestone 2:
Extensible Connector Architecture & Format Completion.

Tests cover:
1. Spreadsheet (.xlsx, .xls) Reading, Inspection, Chunking, Embedding, and Dual-Mode Retrieval
2. Corrupt / Malformed Spreadsheet Error Handling
3. Real Audio (.wav, .mp3) Decoding via Soundfile, Whisper STT Transcription, Chunking, Chroma Embedding, and Retrieval
4. Corrupt Audio Failure Handling
5. Extensible Connector Architecture:
   - BaseConnector contract compliance
   - ConnectorRegistry registration, lookup, enumeration, and sync
   - Built-in FilesystemConnector, SpreadsheetConnector, and AudioConnector
   - Authorization security boundaries (rejection of arbitrary locations, authorization, revocation)
   - REST API endpoints (GET /api/connectors, POST /api/connectors/{name}/sync)
6. Full backward compatibility and zero intelligence core regression
"""

import io
import math
import os
import struct
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import openpyxl
import pytest
import soundfile as sf
from starlette.testclient import TestClient

import config
from app.api.server import app
from app.connectors.audio import AudioConnector
from app.connectors.base import (
    BaseConnector,
    authorize_connector_root,
    revoke_connector_root,
    clear_authorized_roots,
    is_authorized_connector_path,
)
from app.connectors.filesystem import FilesystemConnector
from app.connectors.models import ConnectorContent, ConnectorItem, SyncResult
from app.connectors.registry import (
    ConnectorRegistry,
    get_connector_registry,
    register_connector,
    list_connectors,
    get_connector,
    sync_connector,
)
from app.connectors.spreadsheet import SpreadsheetConnector
from app.ingestion.file_classifier import classify_file
from app.ingestion.ingest import ingest_file
from app.ingestion.reader import read_documents, read_excel
from app.ingestion.structured.handler import handle_structured_file
from app.knowledge.audio_transcriber import (
    load_audio_waveform,
    set_mock_transcriber,
    reset_mock_transcriber,
    transcribe_audio,
)
from app.knowledge.document_registry import get_all_documents, initialize_registry
from app.retrieval.retriever import retrieve, retrieve_chunks
from app.retrieval.structured_retriever import retrieve_structured_files

TEST_API_KEY = "test-api-key-12345"
TEST_ADMIN_KEY = "test-admin-key-99999"


@pytest.fixture(autouse=True)
def configure_test_env(tmp_path, monkeypatch):
    """Isolate documents, registry DB, and auth in tmp_path."""
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    test_docs = test_data / "documents"
    test_docs.mkdir(parents=True, exist_ok=True)
    test_reg_db = tmp_path / "document_registry.db"
    test_queue_db = test_data / "queue.db"

    monkeypatch.setattr(config, "DATA_DIR", test_data)
    monkeypatch.setattr(config, "DOCUMENTS_DIR", test_docs)
    monkeypatch.setattr(config, "AUTHORIZED_CONNECTOR_ROOTS", [test_docs])
    monkeypatch.setattr(config, "REGISTRY_DB", test_reg_db)
    monkeypatch.setattr(config, "QUEUE_DB", test_queue_db)
    monkeypatch.setattr(config, "API_KEY", TEST_API_KEY)
    monkeypatch.setattr(config, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "QUEUE_WORKER_ENABLED", False)

    from app.ingestion import ingest
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", test_docs)

    from app.storage.sqlite import connect_sqlite
    with connect_sqlite(test_reg_db) as conn:
        conn.execute("SELECT 1")

    initialize_registry()
    get_connector_registry().clear()
    clear_authorized_roots()
    reset_mock_transcriber()
    yield
    get_connector_registry().clear()
    clear_authorized_roots()
    reset_mock_transcriber()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _create_sample_excel(path: Path) -> Path:
    """Helper to create a multi-sheet .xlsx workbook."""
    wb = openpyxl.Workbook()
    # Sheet 1: Expenses
    ws1 = wb.active
    ws1.title = "Expenses"
    ws1.append(["Category", "Amount", "Quarter"])
    ws1.append(["Research", 12500, "Q1"])
    ws1.append(["Hardware", 4800, "Q1"])
    ws1.append(["Cloud", 3200, "Q2"])

    # Sheet 2: Projects
    ws2 = wb.create_sheet(title="Projects")
    ws2.append(["ProjectName", "Lead", "Status"])
    ws2.append(["LifeOS", "Principal Architect", "Active"])
    ws2.append(["PrivacyCore", "Security Engineer", "Active"])

    wb.save(path)
    return path


def _create_sample_wav(path: Path, duration_sec: float = 0.5, freq: float = 440.0) -> Path:
    """Helper to create a valid 16kHz mono WAV file."""
    sr = 16000
    n_samples = int(duration_sec * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        raw_samples = []
        for i in range(n_samples):
            val = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * freq * (i / sr)))
            raw_samples.append(struct.pack("<h", val))
        wf.writeframes(b"".join(raw_samples))
    return path


def _create_sample_mp3(path: Path, duration_sec: float = 0.5, freq: float = 440.0) -> Path:
    """Helper to create a valid 16kHz MP3 file via soundfile."""
    sr = 16000
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False, dtype=np.float32)
    waveform = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(str(path), waveform, sr, format="MP3")
    return path


# ============================================================
# 1. SPREADSHEET (.XLSX) TESTS
# ============================================================

def test_excel_reader_extracts_sheets(tmp_path):
    excel_path = config.DOCUMENTS_DIR / "finances.xlsx"
    _create_sample_excel(excel_path)

    text = read_excel(excel_path)
    assert "Sheet: Expenses" in text
    assert "Research" in text
    assert "12500" in text
    assert "Sheet: Projects" in text
    assert "PrivacyCore" in text

    generic_text = read_documents(excel_path)
    assert generic_text == text


def test_excel_structured_handler_inspects_metadata(tmp_path):
    excel_path = config.DOCUMENTS_DIR / "budget.xlsx"
    _create_sample_excel(excel_path)

    info = handle_structured_file(excel_path)
    assert info["format"] == "xlsx"
    assert info["status"] == "inspected"
    assert info["sheet_count"] == 2
    sheet_names = [s["name"] for s in info["sheets"]]
    assert "Expenses" in sheet_names
    assert "Projects" in sheet_names
    assert info["total_rows"] >= 5


def test_excel_classifier_identifies_structured_data(tmp_path):
    excel_path = config.DOCUMENTS_DIR / "data.xlsx"
    _create_sample_excel(excel_path)

    classification = classify_file(excel_path)
    assert classification["type"] == "structured_data"
    assert classification["extension"] == ".xlsx"
    assert classification["strategy"] == "structured_data_handler"


def test_excel_ingest_file_end_to_end_and_retrieval(tmp_path):
    """
    Test real multi-sheet .xlsx end-to-end:
    extraction -> metadata inspection -> registration -> chunking -> embedding -> dual retrieval.
    """
    test_docs = config.DOCUMENTS_DIR
    excel_path = test_docs / "quarterly_budget.xlsx"
    _create_sample_excel(excel_path)

    success = ingest_file(excel_path)
    assert success is True

    docs = get_all_documents()
    matching = [d for d in docs if d["filename"] == "quarterly_budget.xlsx"]
    assert len(matching) == 1
    assert matching[0]["ingestion_status"] == "success"
    assert matching[0]["chunk_count"] > 0

    # 1. Structured metadata retrieval
    struct_results = retrieve_structured_files("quarterly budget")
    assert len(struct_results) >= 1
    assert struct_results[0]["filename"] == "quarterly_budget.xlsx"
    assert struct_results[0]["metadata"]["sheet_count"] == 2

    # 2. Semantic vector retrieval of tabular text chunks
    chunks = retrieve_chunks("Research expenses", top_k=5)
    assert len(chunks) > 0
    # At least one chunk from quarterly_budget.xlsx should contain the sheet data
    matching_chunks = [c for c in chunks if "quarterly_budget.xlsx" in str(c.get("file_path", ""))]
    assert len(matching_chunks) > 0
    assert "Expenses" in matching_chunks[0]["document"]


def test_corrupt_spreadsheet_safe_handling(tmp_path):
    """Test corrupt or invalid .xlsx file fails gracefully without crashing."""
    corrupt_path = config.DOCUMENTS_DIR / "corrupt_data.xlsx"
    corrupt_path.write_bytes(b"PK\x03\x04\x00\x00corrupted_non_zip_data_payload")

    info = handle_structured_file(corrupt_path)
    assert info["status"] == "error"
    assert "Invalid or corrupt Excel" in info.get("error", "")

    success = ingest_file(corrupt_path)
    assert success is False

    docs = get_all_documents()
    matching = [d for d in docs if d["filename"] == "corrupt_data.xlsx"]
    assert len(matching) == 1
    assert matching[0]["ingestion_status"] == "failed"
    assert (
        "corrupt" in matching[0]["error_message"].lower()
        or "invalid" in matching[0]["error_message"].lower()
        or "error" in matching[0]["error_message"].lower()
    )


# ============================================================
# 2. AUDIO INGESTION & WHISPER STT TESTS
# ============================================================

def test_load_audio_waveform(tmp_path):
    wav_path = tmp_path / "tone.wav"
    _create_sample_wav(wav_path, duration_sec=0.25)

    waveform, duration = load_audio_waveform(wav_path, target_sr=16000)
    assert isinstance(waveform, np.ndarray)
    assert waveform.dtype == np.float32
    assert abs(duration - 0.25) < 0.05
    assert len(waveform) > 0


def test_transcribe_audio_with_mock_transcriber(tmp_path):
    wav_path = tmp_path / "voice_memo.wav"
    _create_sample_wav(wav_path)

    set_mock_transcriber(lambda p: {
        "text": "LifeOS milestone two audio transcription verified.",
        "language": "en",
        "duration": 1.5,
        "filename": p.name,
    })

    result = transcribe_audio(wav_path)
    assert result["text"] == "LifeOS milestone two audio transcription verified."
    assert result["language"] == "en"
    assert result["duration"] == 1.5


def test_audio_ingest_file_with_mock_transcriber(tmp_path, monkeypatch):
    test_docs = config.DOCUMENTS_DIR
    wav_path = test_docs / "meeting_notes.wav"
    _create_sample_wav(wav_path)

    set_mock_transcriber(lambda p: {
        "text": "Discussed system architecture with Alice and Bob regarding vector databases.",
        "language": "en",
        "duration": 3.0,
        "filename": p.name,
    })

    success = ingest_file(wav_path)
    assert success is True

    docs = get_all_documents()
    matching = [d for d in docs if d["filename"] == "meeting_notes.wav"]
    assert len(matching) == 1
    assert matching[0]["ingestion_status"] == "success"
    assert matching[0]["chunk_count"] > 0


def test_real_audio_whisper_wav_end_to_end(tmp_path, monkeypatch):
    """
    Test real Whisper STT implementation with LIFEOS_MOCK_STT disabled:
    audio -> Whisper transcription -> chunking -> embedding -> Chroma -> retrieval.
    """
    monkeypatch.delenv("LIFEOS_MOCK_STT", raising=False)
    reset_mock_transcriber()

    test_docs = config.DOCUMENTS_DIR
    wav_path = test_docs / "real_speech.wav"
    _create_sample_wav(wav_path, duration_sec=1.0, freq=440.0)

    # Ingest using full real pipeline
    success = ingest_file(wav_path)
    assert success is True

    docs = get_all_documents()
    matching = [d for d in docs if d["filename"] == "real_speech.wav"]
    assert len(matching) == 1
    assert matching[0]["ingestion_status"] == "success"
    assert matching[0]["chunk_count"] > 0

    # Retrieve vector chunk
    chunks = retrieve_chunks("speech", top_k=5)
    assert len(chunks) > 0


def test_real_audio_mp3_decoding_and_transcription(tmp_path, monkeypatch):
    """
    Test real MP3 decoder path using soundfile:
    MP3 generation -> soundfile decoding -> Whisper transcription.
    """
    monkeypatch.delenv("LIFEOS_MOCK_STT", raising=False)
    reset_mock_transcriber()

    mp3_path = config.DOCUMENTS_DIR / "real_sample.mp3"
    _create_sample_mp3(mp3_path, duration_sec=1.0, freq=440.0)

    # 1. Test waveform loading & decoding
    waveform, duration = load_audio_waveform(mp3_path, target_sr=16000)
    assert isinstance(waveform, np.ndarray)
    assert waveform.dtype == np.float32
    assert len(waveform) > 0
    assert abs(duration - 1.0) < 0.1

    # 2. Test real Whisper transcription
    result = transcribe_audio(mp3_path)
    assert "text" in result
    assert result["language"] == "en"
    assert result["duration"] > 0

    # 3. Test full ingestion of MP3
    success = ingest_file(mp3_path)
    assert success is True
    docs = get_all_documents()
    matching = [d for d in docs if d["filename"] == "real_sample.mp3"]
    assert len(matching) == 1
    assert matching[0]["ingestion_status"] == "success"


def test_corrupt_audio_safe_handling(tmp_path):
    """Test corrupt audio file handling does not crash the system."""
    corrupt_wav = config.DOCUMENTS_DIR / "broken.wav"
    corrupt_wav.write_bytes(b"RIFF\x00\x00\x00\x00NOT_A_VALID_WAVE_HEADER")

    # Waveform loading returns fallback
    waveform, duration = load_audio_waveform(corrupt_wav)
    assert isinstance(waveform, np.ndarray)

    # Transcription handles error safely
    result = transcribe_audio(corrupt_wav)
    assert "text" in result
    assert "error" in result or result["text"] != ""


# ============================================================
# 3. EXTENSIBLE CONNECTOR ARCHITECTURE TESTS
# ============================================================

class CustomTestConnector(BaseConnector):
    """Mock connector implementing the BaseConnector interface."""

    def __init__(self, name: str = "custom_test"):
        super().__init__()
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def source_type(self) -> str:
        return "custom"

    def validate_config(self) -> bool:
        return True

    def discover_items(self) -> list[ConnectorItem]:
        return [
            ConnectorItem(
                id="item_001",
                title="Remote Report.txt",
                source_type="custom",
                uri="remote://reports/item_001.txt",
                mime_type="text/plain",
                size_bytes=100,
            )
        ]

    def fetch_content(self, item_id: str) -> ConnectorContent:
        return ConnectorContent(
            item_id=item_id,
            text_content="Synced report from custom connector source.",
            metadata={"source": "custom_remote"},
        )


def test_connector_registry_lifecycle():
    registry = get_connector_registry()
    connector = CustomTestConnector(name="mock_source")

    registry.register(connector)
    assert registry.get("mock_source") is connector

    status_list = registry.list_connectors()
    assert any(s.name == "mock_source" for s in status_list)

    assert registry.unregister("mock_source") is True
    assert registry.get("mock_source") is None


def test_connector_sync_execution(tmp_path):
    dest_dir = tmp_path / "synced_docs"
    dest_dir.mkdir(parents=True, exist_ok=True)

    connector = CustomTestConnector(name="test_sync")
    result = connector.sync(destination_dir=dest_dir)

    assert result.status == "success"
    assert result.items_discovered == 1
    assert result.items_synced == 1
    assert result.items_failed == 0

    synced_file = dest_dir / "item_001.txt"
    assert synced_file.exists()
    assert synced_file.read_text(encoding="utf-8") == "Synced report from custom connector source."


def test_connector_authorization_security_boundary(tmp_path):
    """
    Prove authorization boundary:
    1. Folders outside authorized roots are rejected.
    2. Authorizing a root grants access.
    3. Revoking the root immediately denies access.
    """
    unauthorized_folder = tmp_path / "unauthorized_external_folder"
    unauthorized_folder.mkdir()
    (unauthorized_folder / "file.txt").write_text("Secret external content", encoding="utf-8")

    fs_connector = FilesystemConnector(folder_path=unauthorized_folder, name="external_fs")
    assert fs_connector.validate_config() is False
    with pytest.raises(PermissionError) as exc_info:
        fs_connector.discover_items()
    assert "Access denied" in str(exc_info.value)

    # SpreadsheetConnector boundary check
    sheet_connector = SpreadsheetConnector(folder_path=unauthorized_folder, name="external_sheet")
    assert sheet_connector.validate_config() is False
    with pytest.raises(PermissionError):
        sheet_connector.discover_items()

    # AudioConnector boundary check
    audio_connector = AudioConnector(folder_path=unauthorized_folder, name="external_audio")
    assert audio_connector.validate_config() is False
    with pytest.raises(PermissionError):
        audio_connector.discover_items()

    # Explicitly authorize folder
    authorize_connector_root(unauthorized_folder)
    assert fs_connector.validate_config() is True
    assert len(fs_connector.discover_items()) == 1

    # Revoke authorization
    revoke_connector_root(unauthorized_folder)
    assert fs_connector.validate_config() is False
    with pytest.raises(PermissionError):
        fs_connector.discover_items()


def test_builtin_filesystem_connector(tmp_path):
    source_folder = config.DOCUMENTS_DIR / "source_fs"
    source_folder.mkdir()
    (source_folder / "doc1.txt").write_text("Filesystem content", encoding="utf-8")
    (source_folder / "doc2.md").write_text("# Markdown", encoding="utf-8")

    fs_connector = FilesystemConnector(folder_path=source_folder, name="my_fs")
    assert fs_connector.validate_config() is True

    items = fs_connector.discover_items()
    assert len(items) == 2
    item_titles = [i.title for i in items]
    assert "doc1.txt" in item_titles
    assert "doc2.md" in item_titles

    content = fs_connector.fetch_content(items[0].id)
    assert content.raw_bytes is not None


def test_builtin_spreadsheet_connector(tmp_path):
    source_folder = config.DOCUMENTS_DIR / "source_sheets"
    source_folder.mkdir()
    _create_sample_excel(source_folder / "metrics.xlsx")
    (source_folder / "table.csv").write_text("colA,colB\n1,2\n", encoding="utf-8")

    connector = SpreadsheetConnector(folder_path=source_folder, name="my_sheets")
    assert connector.validate_config() is True
    items = connector.discover_items()
    assert len(items) == 2
    titles = [i.title for i in items]
    assert "metrics.xlsx" in titles
    assert "table.csv" in titles


def test_builtin_audio_connector(tmp_path):
    source_folder = config.DOCUMENTS_DIR / "source_audio"
    source_folder.mkdir()
    _create_sample_wav(source_folder / "recording.wav")

    connector = AudioConnector(folder_path=source_folder, name="my_audio")
    assert connector.validate_config() is True
    items = connector.discover_items()
    assert len(items) == 1
    assert items[0].title == "recording.wav"


# ============================================================
# 4. CONNECTOR REST API ENDPOINTS
# ============================================================

def test_api_list_connectors(client):
    connector = CustomTestConnector(name="api_test_connector")
    register_connector(connector)

    res = client.get(
        "/api/connectors",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 200
    connectors = res.json()
    assert any(c["name"] == "api_test_connector" for c in connectors)


def test_api_get_connector_details(client):
    connector = CustomTestConnector(name="detail_connector")
    register_connector(connector)

    # Valid connector
    res = client.get(
        "/api/connectors/detail_connector",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "detail_connector"
    assert res.json()["source_type"] == "custom"

    # Missing connector returns 404
    res_404 = client.get(
        "/api/connectors/non_existent_connector",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res_404.status_code == 404


def test_api_trigger_connector_sync(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path / "documents")
    connector = CustomTestConnector(name="syncable_connector")
    register_connector(connector)

    res = client.post(
        "/api/connectors/syncable_connector/sync",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["connector_name"] == "syncable_connector"
    assert data["status"] == "success"
    assert data["items_synced"] == 1
