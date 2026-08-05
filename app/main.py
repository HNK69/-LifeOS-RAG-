
"""
main.py

Entry point of LifeOS.

This file connects all modules together.
"""
from ingestion.reader import read_documents
from processing.chunker import clean_text,chunk_text
from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import store_embeddings
from retrieval.retriever import retrieve
from prompting.prompt_builder import build_prompt

def main():
    text = read_documents("data/documents/Summer_Internship_Schedule.pdf")
    cleaned_text = clean_text(text)
    chunks = chunk_text(cleaned_text)
    embeddings = generate_embeddings(chunks)
    store_embeddings(chunks,embeddings)


    # print(f"\nTotal Chunks: {len(chunks)}\n")

    # for i, chunk in enumerate(chunks, start=1):
    #     print(f"\n{'='*60}")
    #     print(f"Chunk {i}")
    #     print(f"{'='*60}")
    #     print(chunk)




if __name__ == "__main__":
    # main()
    query = 'what is explanibility?'
    results = retrieve(query)
    print(results["documents"][0])
