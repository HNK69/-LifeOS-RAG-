"""
guardrails.py

LifeOS filesystem security and resource guardrails.

Responsibilities:
- Enforce filesystem root containment beneath DOCUMENTS_DIR.
- Reject path traversal, symlink escapes, absolute external paths, and sibling directories.
- Enforce file size admission control before expensive processing / reading into memory.
"""

from pathlib import Path
import os
import logging

logger = logging.getLogger(__name__)


def validate_document_path(file_path, base_dir=None, must_exist=True) -> Path:
    """
    Validate that file_path resolves strictly beneath the configured DOCUMENTS_DIR.

    Enforces filesystem root containment against arbitrary host access, traversal
    (../), symlink escapes, sibling-prefix directory attacks, and nonexistent paths
    when must_exist=True.

    Parameters:
        file_path: The file path to validate (str or Path).
        base_dir: Optional base directory to validate against (defaults to config.DOCUMENTS_DIR).
        must_exist: If True, the file must exist and be a regular file.

    Returns:
        The canonical, resolved Path strictly beneath base_dir.

    Raises:
        ValueError: If path is None, empty, malformed, outside base_dir, or not a regular file.
        FileNotFoundError: If must_exist=True and the file does not exist.
    """
    if file_path is None:
        raise ValueError("Document path cannot be None")

    raw_str = str(file_path).strip()
    if not raw_str:
        raise ValueError("Document path cannot be empty")

    if base_dir is None:
        try:
            from config import DOCUMENTS_DIR
        except ImportError:
            from app.config import DOCUMENTS_DIR
        base_dir = DOCUMENTS_DIR

    try:
        resolved_base = Path(base_dir).resolve()
    except (OSError, RuntimeError, ValueError) as err:
        raise ValueError("Invalid documents directory configuration") from err

    try:
        raw_path = Path(file_path)
    except (TypeError, ValueError) as err:
        raise ValueError("Invalid document path") from err

    # Resolve candidate path:
    try:
        if raw_path.is_absolute():
            resolved_candidate = raw_path.resolve()
        else:
            # For relative paths, check both CWD-relative and base_dir-relative
            cwd_cand = (Path.cwd() / raw_path).resolve()
            base_cand = (resolved_base / raw_path).resolve()
            # If CWD-relative candidate is strictly beneath resolved_base, prefer it
            if _is_strictly_relative_to(cwd_cand, resolved_base):
                resolved_candidate = cwd_cand
            else:
                resolved_candidate = base_cand
    except (OSError, RuntimeError, ValueError) as err:
        raise ValueError("Invalid document path") from err

    # Validate containment: strictly beneath resolved_base (not resolved_base itself)
    if not _is_strictly_relative_to(resolved_candidate, resolved_base):
        raise ValueError("Access denied: path is outside documents directory")

    if must_exist:
        if not resolved_candidate.exists():
            raise FileNotFoundError("Document not found")
        if not resolved_candidate.is_file():
            raise ValueError("Document path must be a regular file")

    return resolved_candidate


def _is_strictly_relative_to(candidate: Path, base: Path) -> bool:
    """
    Helper to check whether candidate is strictly beneath base.
    Handles Windows path semantics and case sensitivity.
    """
    try:
        cand_norm = Path(os.path.normcase(str(candidate)))
        base_norm = Path(os.path.normcase(str(base)))
        return cand_norm.is_relative_to(base_norm) and cand_norm != base_norm
    except (ValueError, TypeError):
        return False


def check_file_size(file_path, max_bytes=None) -> int:
    """
    Check if the file exceeds the configured maximum size limit using stat metadata.

    Raises:
        ValueError: If the file size exceeds the limit.

    Returns:
        The file size in bytes.
    """
    path = Path(file_path)
    if max_bytes is None:
        try:
            from config import get_max_file_size_bytes
        except ImportError:
            from app.config import get_max_file_size_bytes
        max_bytes = get_max_file_size_bytes(path)

    st_size = path.stat().st_size
    if st_size > max_bytes:
        raise ValueError(
            f"File size exceeds maximum allowed limit: {st_size} bytes > {max_bytes} bytes"
        )
    return st_size
