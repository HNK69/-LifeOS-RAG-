from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection
from retrieval.file_index import find_file
from retrieval.structured_retriever import retrieve_structured_files
from retrieval.people_retriever import retrieve_by_person, retrieve_by_people


def retrieve_chunks(query, top_k=10):
    """Retrieve semantic document chunks only."""

    query_embedding = generate_embeddings([query])[0]

    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    retrieved = []

    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        if not doc:
            continue

        retrieved.append({
            "document": doc,
            "source": meta.get("source"),
            "file_path": meta.get("file_path"),
            "chunk_id": meta.get("chunk_id"),
            "distance": dist,
            "type": "chunk",
        })

    return retrieved



def retrieve(query, top_k=3):
    """Retrieve relevant information for general queries."""

    person_query = _extract_people_query(query)

    if person_query:
        if isinstance(person_query, list):
            person_matches = retrieve_by_people(person_query)
        else:
            person_matches = retrieve_by_person(person_query)

        if person_matches:
            return person_matches

    structured_matches = retrieve_structured_files(query)

    if structured_matches:
        return [
            {
                "document": "",
                "source": item["filename"],
                "file_path": item["path"],
                "chunk_id": None,
                "distance": 0.0,
                "type": "structured_file",
                "metadata": item["metadata"],
            }
            for item in structured_matches
        ]

    file_match = find_file(query)

    if file_match:
        return [
            {
                "document": "",
                "source": file_match["name"],
                "file_path": file_match["path"],
                "chunk_id": None,
                "distance": 0.0,
                "type": "file",
            }
        ]

    return retrieve_chunks(query, top_k)


def _extract_people_query(query):
    text = query.lower()

    triggers = [
        "photos with ",
        "pictures with ",
        "images with ",
        "photos of ",
        "pictures of ",
        "images of ",
    ]

    for trigger in triggers:
        if trigger in text:
            names = text.split(trigger, 1)[1].strip()

            if " and " in names:
                return [
                    name.strip()
                    for name in names.split(" and ")
                    if name.strip()
                ]

            return names

    return None