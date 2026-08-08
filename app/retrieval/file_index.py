from pathlib import Path

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

def find_file(query, documents_dir="data/documents"):
    query_words = set(query.lower().replace("-", " ").replace("_", " ").split())

    best_match = None
    best_score = 0

    for file in get_file_index(documents_dir):
        filename = file["stem"].lower().replace("-", " ").replace("_", " ")
        filename_words = set(filename.split())

        score = len(query_words & filename_words)

        if score > best_score:
            best_score = score
            best_match = file

    return best_match if best_score >= 2 else None