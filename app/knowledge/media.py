"""
media.py

Lightweight media classification and metadata extraction.

Does not load media contents into memory.
"""

from pathlib import Path

from config import (
    IMAGE_EXTENSIONS,
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
)


MEDIA_TYPES = {
    "image",
    "audio",
    "video",
}


def get_media_type(file_path):
    """Return image/audio/video or None."""

    extension = Path(file_path).suffix.lower()

    if extension in IMAGE_EXTENSIONS:
        return "image"

    if extension in AUDIO_EXTENSIONS:
        return "audio"

    if extension in VIDEO_EXTENSIONS:
        return "video"

    return None


def is_media_file(file_path):
    """Return True when the file is supported media."""

    return get_media_type(file_path) is not None


def get_media_metadata(file_path):
    """
    Extract lightweight filesystem metadata for media.

    No media decoding or model inference happens here.
    """

    path = Path(file_path).resolve()
    stat = path.stat()

    media_type = get_media_type(path)

    if media_type is None:
        raise ValueError(
            f"Unsupported media file: {path}"
        )

    return {
        "media_type": media_type,
        "filename": path.name,
        "extension": path.suffix.lower(),
        "path": str(path),
        "file_size": stat.st_size,
        "modified_time": stat.st_mtime,
        "modified_time_ns": stat.st_mtime_ns,
    }