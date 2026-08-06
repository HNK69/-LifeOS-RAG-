
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
from llm.generator import generate_response



def main():
    text = read_documents("data/documents/Ml Course Schedule.docx")
    cleaned_text = clean_text(text)
    chunks = chunk_text(cleaned_text)
    embeddings = generate_embeddings(chunks)
    store_embeddings(chunks,embeddings)
    query = "When will I study RAG?"
    results = retrieve(query)
    retrieved_chunks = results["documents"][0]
    # print(retrieved_chunks)
    prompt = build_prompt(query,retrieved_chunks)
    # print(prompt)
    response = generate_response(prompt)
    print("\nAnswer:\n")
    print(response)

    # print(len(chunks))
    # print(len(cleaned_text))

    # print(f"\nTotal Chunks: {len(chunks)}\n")

    # for i, chunk in enumerate(chunks, start=1):
    #     print(f"\n{'='*60}")
    #     print(f"Chunk {i}")
    #     print(f"{'='*60}")
    #     print(chunk)


if __name__ == "__main__":
    main()
