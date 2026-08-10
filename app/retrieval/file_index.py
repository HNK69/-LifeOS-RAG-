from pathlib import Path
import json

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".csv",
    ".json",
}


def get_file_index(documents_dir="data/documents"):
    files = []

    for path in Path(documents_dir).iterdir():
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append({
                "name": path.name,
                "stem": path.stem,
                "path": str(path),
                "extension": path.suffix.lower(),
            })

    return files


def _extract_text(path):
    """Extract searchable text from a supported file."""

    suffix = path.suffix.lower()

    try:
        if suffix in {".txt", ".md"}:
            return path.read_text(
                encoding="utf-8",
                errors="ignore",
            )

        if suffix == ".json":
            data = json.loads(
                path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )
            )
            return json.dumps(data)

        if suffix == ".csv":
            return path.read_text(
                encoding="utf-8",
                errors="ignore",
            )

        if suffix == ".docx":
            from docx import Document

            document = Document(path)

            return "\n".join(
                paragraph.text
                for paragraph in document.paragraphs
            )

        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))

            return "\n".join(
                page.extract_text() or ""
                for page in reader.pages
            )

    except Exception:
        return ""

    return ""


def _tokenize(text):
    return {
        word
        for word in (
            text.lower()
            .replace("-", " ")
            .replace("_", " ")
            .replace("/", " ")
            .replace("\\", " ")
            .replace(".", " ")
            .replace(",", " ")
            .split()
        )
        if len(word) > 2
    }


def find_file(query, documents_dir="data/documents"):
    """
    Find the most relevant file using filename/content evidence.
    Requires at least one meaningful query-token match.
    """

    query_words = _tokenize(query)

    if not query_words:
        return None

    best_match = None
    best_score = 0.0

    for file in get_file_index(documents_dir):
        path = Path(file["path"])

        filename_words = _tokenize(file["stem"])
        content_words = _tokenize(_extract_text(path))

        filename_matches = query_words & filename_words
        content_matches = query_words & content_words

        score = (
            len(filename_matches) * 5
            + len(content_matches) * 1.5
        )

        if score > best_score:
            best_score = score
            best_match = file

    return best_match if best_score > 0 else None
