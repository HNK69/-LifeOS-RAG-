"""
reader.py

Purpose:
--------
This file is responsible for converting different document types
(TXT, PDF, DOCX, etc.) into plain text.

Why?
----
The rest of our RAG pipeline only understands text.
No matter what file the user uploads, this module should always
return a string.
"""

from pathlib import Path
from pypdf import PdfReader
from docx import Document
import pandas as pd
import json
import io

try:
    from ingestion.guardrails import validate_document_path, check_file_size
except ImportError:
    from app.ingestion.guardrails import validate_document_path, check_file_size

try:
    from config import get_max_file_size_bytes
except ImportError:
    from app.config import get_max_file_size_bytes


def read_text(file_path):
    path = Path(file_path)
    max_bytes = get_max_file_size_bytes(path)
    check_file_size(path, max_bytes=max_bytes)

    with open(path, "rb") as file:
        raw_bytes = file.read(max_bytes + 1)

    if len(raw_bytes) > max_bytes:
        raise ValueError(
            f"File size exceeds maximum allowed limit: > {max_bytes} bytes"
        )

    return io.TextIOWrapper(io.BytesIO(raw_bytes), encoding="utf-8").read()


def read_pdf(file_path):
    path = Path(file_path)
    check_file_size(path)
    reader = PdfReader(path)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()   
        if page_text:
            text += page_text + "\n"
    return text


def read_docx(file_path):
    path = Path(file_path)
    check_file_size(path)
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def read_md(file_path):
    return read_text(file_path)


def read_csv(file_path):
    path = Path(file_path)
    check_file_size(path)
    dataframe = pd.read_csv(path)
    return dataframe.to_string(index=False)


def read_json(file_path):
    path = Path(file_path)
    check_file_size(path)
    with open(path, 'r', encoding='utf-8') as file:
        data = json.load(file)
    return json.dumps(data, indent=2)


def read_documents(file_path):
    path = validate_document_path(file_path, must_exist=True)
    check_file_size(path)
    extension = path.suffix.lower()
    if extension == '.txt':
        return read_text(path)
    elif extension == '.pdf':
        return read_pdf(path)
    elif extension == '.docx':
        return read_docx(path)
    elif extension == '.md':
        return read_md(path)
    elif extension == '.csv':
        return read_csv(path)
    elif extension == '.json':
        return read_json(path)
    
    raise ValueError(f"Unsupported file type: {extension}")

# content = read_documents('C:/Users/hnkru/OneDrive/Desktop/LifeOS/data/documents/chattisgarh ideas..md')
# print(content)