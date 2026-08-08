
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
from pathlib import Path



def main():

    documents_dir = Path("data/documents")

    for file_path in documents_dir.iterdir():
        if file_path.is_file():
            text = read_documents(str(file_path))
            cleaned_text = clean_text(text)
            chunks = chunk_text(cleaned_text)
            embeddings = generate_embeddings(chunks)
            store_embeddings(chunks, embeddings, str(file_path))
    query = "When will I study RAG?"    
    results = retrieve(query)
    # print(results)
    print("\nRetrieval Ranking:")
    for i, item in enumerate(results, start=1):
        print(
            f"{i}. {item['source']} | "
            f"chunk={item['chunk_id']} | "
            f"distance={item['distance']:.4f}"
        )

    for items in results:
        print(f"Found file: {items['source']}")
        print(f"Path: {items['file_path']}")

    retrieved_chunks = [item["document"] for item in results]
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
