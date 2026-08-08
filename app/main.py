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

    seen = set()

    for item in results:
        source = item["source"]
        file_path = str(item["file_path"]).replace("\\", "/")

        key = (source.lower(), file_path.lower())

        if key in seen:
            continue

        seen.add(key)

        print(f"- {source}")
        print(f"  {file_path}")


if __name__ == "__main__":
    main()