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

def read_text(file_path):

    with open(file_path,'r',encoding='utf-8') as file:
        content=file.read()

    return content

def read_pdf(file_path):
    reader=PdfReader(file_path)
    text=""
    for page in reader.pages:
        page_text=page.extract_text()   
        if page_text:
            text += page_text + "\n"
    return text

def read_docx(file_path):
    document = Document(file_path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)

def read_md(file_path):
    return read_text(file_path)

def read_csv(file_path):
    dataframe = pd.read_csv(file_path)
    return dataframe.to_string(index=False)

def read_json(file_path):

    with open(file_path,'r',encoding='utf-8') as file:
        data=json.load(file)
    return json.dumps(data,indent=2)


def read_documents(file_path):

    path = Path(file_path)
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