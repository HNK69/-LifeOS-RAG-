
"""
main.py

Entry point of LifeOS.

This file connects all modules together.
"""

from retrieval.retriever import retrieve
from prompting.prompt_builder import build_prompt
from llm.generator import generate_response


def main():

    query = input("Ask LifeOS: ")

    results = retrieve(query)

    print("\nRetrieval Ranking:")

    for i, item in enumerate(results, start=1):
        print(
            f"{i}. {item['source']} | "
            f"chunk={item['chunk_id']} | "
            f"distance={item['distance']:.4f}"
        )

    retrieved_chunks = [
        item["document"]
        for item in results
        if item["document"]
    ]

    prompt = build_prompt(query, retrieved_chunks)

    response = generate_response(prompt)

    print("\nAnswer:\n")
    print(response)

    print("\nSources:")

    for item in results:
        print(f"- {item['source']}")
        print(f"  {item['file_path']}")


if __name__ == "__main__":
    main()