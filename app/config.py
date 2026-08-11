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
MAX_TEXT_FILE_SIZE_MB = 50

MAX_TEXT_FILE_SIZE_BYTES = MAX_TEXT_FILE_SIZE_MB * 1024 * 1024

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

LLM_TIMEOUT_SECONDS = 60

LLM_MAX_RETRIES = 2


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv(
    "LIFEOS_LOG_LEVEL",
    "INFO",
)