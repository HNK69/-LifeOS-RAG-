"""
LifeOS central configuration.

All shared paths, limits, supported formats, and runtime settings
should be defined here so the rest of the application does not
hard-code them.
"""

from pathlib import Path
import os


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"

CHROMA_DIR = BASE_DIR / "chroma_db"
REGISTRY_DB = BASE_DIR / "document_registry.db"


# ============================================================
# FILE TYPES
# ============================================================

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xlsx",
    ".xls",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
}


STRUCTURED_EXTENSIONS = {
    ".csv",
    ".json",
    ".xlsx",
    ".xls",
}


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
}


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
}


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".pdf",
    ".docx",
}


# ============================================================
# INGESTION
# ============================================================

# Files larger than this should be handled carefully rather
# than blindly loaded into memory.
MAX_TEXT_FILE_SIZE_MB = int(os.getenv("LIFEOS_MAX_TEXT_FILE_SIZE_MB", "50"))
MAX_TEXT_FILE_SIZE_BYTES = MAX_TEXT_FILE_SIZE_MB * 1024 * 1024

MAX_STRUCTURED_FILE_SIZE_MB = int(os.getenv("LIFEOS_MAX_STRUCTURED_FILE_SIZE_MB", "50"))
MAX_STRUCTURED_FILE_SIZE_BYTES = MAX_STRUCTURED_FILE_SIZE_MB * 1024 * 1024

MAX_IMAGE_FILE_SIZE_MB = int(os.getenv("LIFEOS_MAX_IMAGE_FILE_SIZE_MB", "20"))
MAX_IMAGE_FILE_SIZE_BYTES = MAX_IMAGE_FILE_SIZE_MB * 1024 * 1024

MAX_MEDIA_FILE_SIZE_MB = int(os.getenv("LIFEOS_MAX_MEDIA_FILE_SIZE_MB", "100"))
MAX_MEDIA_FILE_SIZE_BYTES = MAX_MEDIA_FILE_SIZE_MB * 1024 * 1024

MAX_FILE_SIZE_MB = int(os.getenv("LIFEOS_MAX_FILE_SIZE_MB", "50"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


def get_max_file_size_bytes(file_path) -> int:
    """Return the configured maximum allowed file size in bytes for a given file path."""
    suffix = Path(file_path).suffix.lower()
    if suffix in TEXT_EXTENSIONS or suffix in {".pptx", ".ppt"}:
        return MAX_TEXT_FILE_SIZE_BYTES
    if suffix in STRUCTURED_EXTENSIONS:
        return MAX_STRUCTURED_FILE_SIZE_BYTES
    if suffix in IMAGE_EXTENSIONS:
        return MAX_IMAGE_FILE_SIZE_BYTES
    if suffix in AUDIO_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
        return MAX_MEDIA_FILE_SIZE_BYTES
    return MAX_FILE_SIZE_BYTES


# Embedding batch size.
EMBEDDING_BATCH_SIZE = 16

# Maximum number of chunks stored in one Chroma operation.
CHROMA_BATCH_SIZE = 100


# ============================================================
# CHUNKING
# ============================================================

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


# ============================================================
# EMBEDDINGS
# ============================================================

EMBEDDING_MODEL = os.getenv(
    "LIFEOS_EMBEDDING_MODEL",
    "BAAI/bge-m3",
)


# ============================================================
# RETRIEVAL
# ============================================================

DEFAULT_TOP_K = 3


# ============================================================
# WATCHER
# ============================================================

# Small delay used to allow bursts of filesystem events to settle.
WATCHER_DEBOUNCE_SECONDS = 2.0

# Prevent the watcher from repeatedly processing the same path
# during rapid filesystem changes.
WATCHER_EVENT_COOLDOWN_SECONDS = 5.0

# Watcher file stability settings
WATCHER_STABILITY_SECONDS = float(
    os.getenv("LIFEOS_WATCHER_STABILITY_SECONDS", "1.0")
)

WATCHER_CHECK_INTERVAL_SECONDS = float(
    os.getenv("LIFEOS_WATCHER_CHECK_INTERVAL_SECONDS", "0.25")
)

WATCHER_MAX_STABILITY_WAIT_SECONDS = float(
    os.getenv("LIFEOS_WATCHER_MAX_STABILITY_WAIT_SECONDS", "5.0")
)


# ============================================================
# LLM / OPENROUTER
# ============================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openrouter/free",
)

OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL",
    "https://openrouter.ai/api/v1",
)

LLM_TIMEOUT_SECONDS = float(
    os.getenv("LIFEOS_LLM_TIMEOUT_SECONDS", "60.0")
)

LLM_MAX_RETRIES = int(
    os.getenv("LIFEOS_LLM_MAX_RETRIES", "2")
)


# ============================================================
# AGENT
# ============================================================

AGENT_TIMEOUT_SECONDS = float(
    os.getenv("LIFEOS_AGENT_TIMEOUT_SECONDS", "30.0")
)

# Maximum bounded worker threads for asynchronous/timeout agent execution.
# Capping this pool prevents unbounded thread accumulation when synchronous
# operations block or stall.
AGENT_MAX_WORKERS = int(
    os.getenv("LIFEOS_AGENT_MAX_WORKERS", "4")
)


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv(
    "LIFEOS_LOG_LEVEL",
    "INFO",
)

FACE_MATCH_THRESHOLD = float(
    os.getenv("FACE_MATCH_THRESHOLD", "0.50")
)