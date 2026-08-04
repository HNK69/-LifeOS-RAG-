
"""
main.py

Entry point of LifeOS.

This file connects all modules together.
"""
from ingestion.reader import read_documents
from processing.chunker import clean_text,chunk_text
from embeddings.embedder import generate_embeddings

def main():
    text = read_documents("data/documents/Summer_Internship_Schedule.pdf")
    cleaned_text = clean_text(text)
    chunks = chunk_text(cleaned_text)
    embeddings = generate_embeddings(chunks)


    # print(f"\nTotal Chunks: {len(chunks)}\n")

    # for i, chunk in enumerate(chunks, start=1):
    #     print(f"\n{'='*60}")
    #     print(f"Chunk {i}")
    #     print(f"{'='*60}")
    #     print(chunk)
    print("\nFirst Chunk:\n")
    print(chunks[0])

    print("\nFirst Embedding (First 10 Values):\n")
    print(embeddings[0][:10])


if __name__ == "__main__":
    main()
