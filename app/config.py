"""
LifeOS central configuration.

All shared paths, limits, supported formats, and runtime settings
should be defined here so the rest of the application does not
hard-code them.
"""

from pathlib import Path
import os
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()


# ============================================================
# CONFIGURATION HELPERS & VALIDATION
# ============================================================

def _get_env_int(key: str, default: int, min_val: int = None, max_val: int = None) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        res = default
    else:
        try:
            res = int(val.strip())
        except ValueError:
            raise ValueError(f"Invalid configuration for {key}: '{val}' is not a valid integer")
    if min_val is not None and res < min_val:
        raise ValueError(f"Invalid configuration for {key}: must be >= {min_val}, got {res}")
    if max_val is not None and res > max_val:
        raise ValueError(f"Invalid configuration for {key}: must be <= {max_val}, got {res}")
    return res


def _get_env_float(key: str, default: float, min_val: float = None, max_val: float = None) -> float:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        res = default
    else:
        try:
            res = float(val.strip())
        except ValueError:
            raise ValueError(f"Invalid configuration for {key}: '{val}' is not a valid float")
    if min_val is not None and res < min_val:
        raise ValueError(f"Invalid configuration for {key}: must be >= {min_val}, got {res}")
    if max_val is not None and res > max_val:
        raise ValueError(f"Invalid configuration for {key}: must be <= {max_val}, got {res}")
    return res


def _get_env_str(key: str, default: str, allowed: set = None) -> str:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        res = default
    else:
        res = val.strip()
    if allowed is not None and res not in allowed:
        raise ValueError(f"Invalid configuration for {key}: must be one of {sorted(allowed)}, got '{res}'")
    return res


def _get_env_path(key: str, default: Path) -> Path:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return Path(default)
    return Path(val.strip())


def _get_env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    v = val.strip().lower()
    if v in {"true", "1", "yes", "y", "on"}:
        return True
    if v in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid configuration for {key}: '{val}' is not a valid boolean")


def _get_env_list(key: str, default: list) -> list:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return list(default)
    items = [item.strip() for item in val.replace(";", ",").split(",") if item.strip()]
    return items if items else list(default)


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = _get_env_path("LIFEOS_DATA_DIR", BASE_DIR / "data")
DOCUMENTS_DIR = _get_env_path("LIFEOS_DOCUMENTS_DIR", DATA_DIR / "documents")

CHROMA_DIR = _get_env_path("LIFEOS_CHROMA_DIR", BASE_DIR / "chroma_db")
REGISTRY_DB = _get_env_path("LIFEOS_REGISTRY_DB", BASE_DIR / "document_registry.db")
ENTITY_DB = _get_env_path("LIFEOS_ENTITY_DB", DATA_DIR / "entities.db")
PEOPLE_DB = _get_env_path("LIFEOS_PEOPLE_DB", DATA_DIR / "people_registry.db")
CONTEXT_DB = _get_env_path("LIFEOS_CONTEXT_DB", DATA_DIR / "context.db")


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
MAX_TEXT_FILE_SIZE_MB = _get_env_int("LIFEOS_MAX_TEXT_FILE_SIZE_MB", 50, min_val=1)
MAX_TEXT_FILE_SIZE_BYTES = MAX_TEXT_FILE_SIZE_MB * 1024 * 1024

MAX_STRUCTURED_FILE_SIZE_MB = _get_env_int("LIFEOS_MAX_STRUCTURED_FILE_SIZE_MB", 50, min_val=1)
MAX_STRUCTURED_FILE_SIZE_BYTES = MAX_STRUCTURED_FILE_SIZE_MB * 1024 * 1024

MAX_IMAGE_FILE_SIZE_MB = _get_env_int("LIFEOS_MAX_IMAGE_FILE_SIZE_MB", 20, min_val=1)
MAX_IMAGE_FILE_SIZE_BYTES = MAX_IMAGE_FILE_SIZE_MB * 1024 * 1024

MAX_MEDIA_FILE_SIZE_MB = _get_env_int("LIFEOS_MAX_MEDIA_FILE_SIZE_MB", 100, min_val=1)
MAX_MEDIA_FILE_SIZE_BYTES = MAX_MEDIA_FILE_SIZE_MB * 1024 * 1024

MAX_FILE_SIZE_MB = _get_env_int("LIFEOS_MAX_FILE_SIZE_MB", 50, min_val=1)
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
EMBEDDING_BATCH_SIZE = _get_env_int("LIFEOS_EMBEDDING_BATCH_SIZE", 16, min_val=1)

# Maximum number of chunks stored in one Chroma operation.
CHROMA_BATCH_SIZE = _get_env_int("LIFEOS_CHROMA_BATCH_SIZE", 100, min_val=1)


# ============================================================
# CHUNKING
# ============================================================

CHUNK_SIZE = _get_env_int("LIFEOS_CHUNK_SIZE", 1000, min_val=50)
CHUNK_OVERLAP = _get_env_int("LIFEOS_CHUNK_OVERLAP", 150, min_val=0)


# ============================================================
# EMBEDDINGS
# ============================================================

EMBEDDING_MODEL = _get_env_str(
    "LIFEOS_EMBEDDING_MODEL",
    "BAAI/bge-m3",
)


# ============================================================
# RETRIEVAL
# ============================================================

DEFAULT_TOP_K = _get_env_int("LIFEOS_DEFAULT_TOP_K", 3, min_val=1)


# ============================================================
# WATCHER
# ============================================================

# Small delay used to allow bursts of filesystem events to settle.
WATCHER_DEBOUNCE_SECONDS = _get_env_float("LIFEOS_WATCHER_DEBOUNCE_SECONDS", 2.0, min_val=0.01)

# Prevent the watcher from repeatedly processing the same path
# during rapid filesystem changes.
WATCHER_EVENT_COOLDOWN_SECONDS = _get_env_float("LIFEOS_WATCHER_EVENT_COOLDOWN_SECONDS", 5.0, min_val=0.01)

# Watcher file stability settings
WATCHER_STABILITY_SECONDS = _get_env_float(
    "LIFEOS_WATCHER_STABILITY_SECONDS",
    1.0,
    min_val=0.01,
)

WATCHER_CHECK_INTERVAL_SECONDS = _get_env_float(
    "LIFEOS_WATCHER_CHECK_INTERVAL_SECONDS",
    0.25,
    min_val=0.01,
)

WATCHER_MAX_STABILITY_WAIT_SECONDS = _get_env_float(
    "LIFEOS_WATCHER_MAX_STABILITY_WAIT_SECONDS",
    5.0,
    min_val=0.01,
)


# ============================================================
# LLM / OPENROUTER
# ============================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_MODEL = (
    os.getenv("OPENROUTER_MODEL")
    or os.getenv("OPENROUTER_ROUTER_MODEL")
    or "openrouter/free"
)

OPENROUTER_VISION_MODEL = _get_env_str(
    "OPENROUTER_VISION_MODEL",
    "google/gemma-4-31b-it",
)

OPENROUTER_BASE_URL = _get_env_str(
    "OPENROUTER_BASE_URL",
    "https://openrouter.ai/api/v1",
)

LLM_TIMEOUT_SECONDS = _get_env_float(
    "LIFEOS_LLM_TIMEOUT_SECONDS",
    60.0,
    min_val=0.1,
)

LLM_MAX_RETRIES = _get_env_int(
    "LIFEOS_LLM_MAX_RETRIES",
    2,
    min_val=0,
)


# ============================================================
# AGENT
# ============================================================

AGENT_TIMEOUT_SECONDS = _get_env_float(
    "LIFEOS_AGENT_TIMEOUT_SECONDS",
    30.0,
    min_val=0.1,
)

# Maximum bounded worker threads for asynchronous/timeout agent execution.
# Capping this pool prevents unbounded thread accumulation when synchronous
# operations block or stall.
AGENT_MAX_WORKERS = _get_env_int(
    "LIFEOS_AGENT_MAX_WORKERS",
    4,
    min_val=1,
)


# ============================================================
# LOGGING & IDENTITY
# ============================================================

LOG_LEVEL = _get_env_str(
    "LIFEOS_LOG_LEVEL",
    "INFO",
    allowed={"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
)

FACE_MATCH_THRESHOLD = _get_env_float(
    "FACE_MATCH_THRESHOLD",
    0.50,
    min_val=0.0,
    max_val=1.0,
)


# ============================================================
# API & PRODUCT LAYER (PHASE 11)
# ============================================================

API_HOST = _get_env_str("LIFEOS_API_HOST", "127.0.0.1")
API_PORT = _get_env_int("LIFEOS_API_PORT", 8000, min_val=1024, max_val=65535)
API_WORKERS = _get_env_int("LIFEOS_API_WORKERS", 1, min_val=1, max_val=16)

# In production, API authentication should always be enabled.
# For testing or special local environments, LIFEOS_API_AUTH_ENABLED can toggle this.
API_AUTH_ENABLED = _get_env_bool("LIFEOS_API_AUTH_ENABLED", True)

# Primary API key for clients
API_KEY = os.getenv("LIFEOS_API_KEY", "")
# Distinct administrative API key for elevated operations like reindexing
API_ADMIN_KEY = os.getenv("LIFEOS_API_ADMIN_KEY", "")

# Rate limiting
API_RATE_LIMIT = _get_env_int("LIFEOS_API_RATE_LIMIT", 60, min_val=1)
API_RATE_WINDOW_SECONDS = _get_env_int("LIFEOS_API_RATE_WINDOW_SECONDS", 60, min_val=1)

# Request size limits (default 10MB)
API_MAX_REQUEST_BYTES = _get_env_int("LIFEOS_API_MAX_REQUEST_BYTES", 10 * 1024 * 1024, min_val=1024)

# Maximum query length in characters
API_MAX_QUERY_LENGTH = _get_env_int("LIFEOS_API_MAX_QUERY_LENGTH", 4096, min_val=1)

# CORS and Network
API_CORS_ORIGINS = _get_env_list(
    "LIFEOS_API_CORS_ORIGINS",
    ["http://localhost:3000", "http://127.0.0.1:3000"],
)
API_ALLOWED_METHODS = _get_env_list(
    "LIFEOS_API_ALLOWED_METHODS",
    ["GET", "POST", "DELETE", "OPTIONS"],
)
API_ALLOWED_HEADERS = _get_env_list(
    "LIFEOS_API_ALLOWED_HEADERS",
    ["*"],
)
API_TRUSTED_HOSTS = _get_env_list(
    "LIFEOS_API_TRUSTED_HOSTS",
    ["localhost", "127.0.0.1", "testserver"],
)

API_LOG_REQUESTS = _get_env_bool("LIFEOS_API_LOG_REQUESTS", True)
API_ENVIRONMENT = _get_env_str(
    "LIFEOS_API_ENVIRONMENT",
    "development",
    allowed={"development", "testing", "staging", "production"},
)


# ============================================================
# PRODUCTION & OPERATIONS LAYER (PHASE 12)
# ============================================================

BACKUP_DIR = _get_env_path("LIFEOS_BACKUP_DIR", DATA_DIR / "backups")
LOG_FORMAT = _get_env_str("LIFEOS_LOG_FORMAT", "text", allowed={"text", "json"})
WATCHER_ENABLED = _get_env_bool("LIFEOS_WATCHER_ENABLED", False)
METRICS_ENABLED = _get_env_bool("LIFEOS_METRICS_ENABLED", True)


# ============================================================
# VALIDATION
# ============================================================

def validate_config():
    """Perform cross-field configuration sanity checks."""
    if CHUNK_OVERLAP >= CHUNK_SIZE:
        raise ValueError(
            f"Invalid configuration: CHUNK_OVERLAP ({CHUNK_OVERLAP}) "
            f"must be strictly less than CHUNK_SIZE ({CHUNK_SIZE})"
        )
    if WATCHER_CHECK_INTERVAL_SECONDS > WATCHER_STABILITY_SECONDS:
        raise ValueError(
            f"Invalid configuration: WATCHER_CHECK_INTERVAL_SECONDS ({WATCHER_CHECK_INTERVAL_SECONDS}) "
            f"must not exceed WATCHER_STABILITY_SECONDS ({WATCHER_STABILITY_SECONDS})"
        )
    if WATCHER_MAX_STABILITY_WAIT_SECONDS < WATCHER_STABILITY_SECONDS:
        raise ValueError(
            f"Invalid configuration: WATCHER_MAX_STABILITY_WAIT_SECONDS ({WATCHER_MAX_STABILITY_WAIT_SECONDS}) "
            f"must be >= WATCHER_STABILITY_SECONDS ({WATCHER_STABILITY_SECONDS})"
        )
    if API_ENVIRONMENT == "production":
        if API_WORKERS > 1:
            raise ValueError(
                f"Invalid configuration: Multi-worker execution (LIFEOS_API_WORKERS={API_WORKERS}) "
                "is not supported in single-process architecture. Must be 1."
            )
        if not API_AUTH_ENABLED:
            raise ValueError(
                "Invalid configuration: LIFEOS_API_AUTH_ENABLED cannot be disabled in production environment"
            )
        if not API_KEY or len(API_KEY.strip()) < 16:
            raise ValueError(
                "Invalid configuration: LIFEOS_API_KEY must be configured with at least 16 characters in production environment"
            )
        if not API_ADMIN_KEY or len(API_ADMIN_KEY.strip()) < 16:
            raise ValueError(
                "Invalid configuration: LIFEOS_API_ADMIN_KEY must be configured with at least 16 characters in production environment"
            )
        if API_KEY == API_ADMIN_KEY:
            raise ValueError(
                "Invalid configuration: LIFEOS_API_KEY and LIFEOS_API_ADMIN_KEY must be distinct in production environment"
            )
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not openrouter_key:
            raise ValueError(
                "Invalid configuration: OPENROUTER_API_KEY must be configured in production environment"
            )
        if "*" in API_CORS_ORIGINS:
            raise ValueError(
                "Invalid configuration: Wildcard '*' in LIFEOS_API_CORS_ORIGINS is forbidden in production environment"
            )
        if "*" in API_TRUSTED_HOSTS or "testserver" in API_TRUSTED_HOSTS:
            raise ValueError(
                "Invalid configuration: Wildcard '*' or 'testserver' in LIFEOS_API_TRUSTED_HOSTS is forbidden in production environment"
            )


validate_config()
