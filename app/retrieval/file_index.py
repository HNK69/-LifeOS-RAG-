"""
file_index.py

Fast file-level discovery for LifeOS.

File discovery should NOT read every document on every query.
Content-based questions are handled by the semantic retrieval layer.

This module focuses on:
- filename matching
- recursive file discovery
- cheap metadata
- indexed registry lookup when available
"""

from pathlib import Path

from config import (
    DOCUMENTS_DIR,
    SUPPORTED_EXTENSIONS,
)


def get_file_index(documents_dir=DOCUMENTS_DIR):
    """
    Recursively discover supported files.

    This only reads filesystem metadata.
    It does NOT open or read document contents.
    """

    documents_dir = Path(
        documents_dir
    ).resolve()

    files = []

    if not documents_dir.exists():
        return files

    for path in documents_dir.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        files.append(
            {
                "name": path.name,
                "stem": path.stem,
                "path": str(path),
                "extension": path.suffix.lower(),
                "size": path.stat().st_size,
            }
        )

    return files


def _tokenize(text):
    """
    Convert text into normalized search tokens.
    """

    return {
        word
        for word in (
            str(text)
            .lower()
            .replace("-", " ")
            .replace("_", " ")
            .replace("/", " ")
            .replace("\\", " ")
            .replace(".", " ")
            .replace(",", " ")
            .replace("(", " ")
            .replace(")", " ")
            .split()
        )
        if len(word) > 2
    }


def _filename_score(query_words, file):
    """
    Score a file using filename evidence only.

    Filename matches receive higher weight because this module
    is specifically responsible for file discovery.
    """

    filename_words = _tokenize(
        file["stem"]
    )

    exact_matches = (
        query_words & filename_words
    )

    if not exact_matches:
        return 0.0

    return len(exact_matches) * 10.0


def find_file(
    query,
    documents_dir=DOCUMENTS_DIR,
):
    """
    Find the best matching file using filename evidence.

    Important:
    This function deliberately does NOT open document contents.

    Semantic/content questions should go through ChromaDB retrieval.
    """

    query_words = _tokenize(
        query
    )

    if not query_words:
        return None

    best_match = None
    best_score = 0.0

    for file in get_file_index(
        documents_dir
    ):

        score = _filename_score(
            query_words,
            file,
        )

        if score > best_score:

            best_score = score
            best_match = file

        elif (
            score == best_score
            and score > 0
            and best_match is not None
        ):

            # Prefer the shorter filename when the match strength
            # is identical.
            if len(file["name"]) < len(
                best_match["name"]
            ):
                best_match = file

    if best_score <= 0:
        return None

    return best_match