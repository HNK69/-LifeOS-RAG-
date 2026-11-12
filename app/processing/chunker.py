import re
from langchain_text_splitters import RecursiveCharacterTextSplitter

try:
    from config import CHUNK_SIZE, CHUNK_OVERLAP
except ImportError:
    from app.config import CHUNK_SIZE, CHUNK_OVERLAP

def clean_text(text):
    text = re.sub(r"[ \t]+", " ", text)      # collapse spaces/tabs
    text = re.sub(r"\n{3,}", "\n\n", text)   # preserve paragraphs
    text = text.strip()
    return text

# print(cleaned_text)

def chunk_text(text, chunk_size=None, chunk_overlap=None):
    if chunk_size is None:
        chunk_size = CHUNK_SIZE
    if chunk_overlap is None:
        chunk_overlap = CHUNK_OVERLAP
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=[
            "\n\n",   # paragraph
            "\n",     # line
            ". ",     # sentence
            "; ",     # semicolon
            ", ",     # comma
            " ",      # word
            ""
        ],
        keep_separator=True,
    )
    return splitter.split_text(text)







#     chunks=[]
#     start=0

#     while start < len(text):

#         end = start+chunk_size # Tentative end of the chunk.

#         if end>=len(text):
#             chunks.append(text[start:].strip())   # If we've reached the end of the document,store the remaining text and stop.
#             break

#         while end > start and text[end] != " ":  # Move backwards until we find a space.This prevents splitting words in half.
#             end -= 1

#         if end == start:
#             end = start + chunk_size          # If no space was found (very long word),fall back to the original chunk size.

#         chunks.append(text[start:end].strip())         # Store the cleaned chunk.

#         start = end-overlap

#         if start < 0:
#             start = 0

#     return chunks

# chunks = chunk_text(cleaned_text)

# for i, chunk in enumerate(chunks, start=1):
#     print(f"\nChunk {i}")
#     print(chunk)

# text = "LifeOS is my personal AI operating system. " * 100

# chunks = chunk_text(text)

# for i, chunk in enumerate(chunks, start=1):
#     print(f"\nChunk {i} ({len(chunk)} chars)")
#     print(chunk[-80:])   # Print the last 80 chars to verify words aren't cut.
