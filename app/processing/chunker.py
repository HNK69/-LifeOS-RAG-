import re
from langchain_text_splitters import RecursiveCharacterTextSplitter

def clean_text(text):

    text=re.sub(r"\s+"," ",text )
    text = text.strip()
    return text

# print(cleaned_text)

def chunk_text(text):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        separators=[
            "\n\n",
            "\n",
            ". ",
            ""
        ]
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
