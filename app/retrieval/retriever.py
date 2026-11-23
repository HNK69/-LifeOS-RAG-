from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection
from retrieval.file_index import find_file
from retrieval.structured_retriever import retrieve_structured_files
from retrieval.people_retriever import retrieve_by_person, retrieve_by_people


def retrieve_chunks(query, top_k=10, workspace_id=None):
    """Retrieve semantic document chunks only, optionally filtered by workspace_id."""

    query_embedding = generate_embeddings([query])[0]

    query_kwargs = {
        "query_embeddings": [query_embedding.tolist()],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if workspace_id:
        query_kwargs["where"] = {"workspace_id": workspace_id}

    results = collection.query(**query_kwargs)

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
            "workspace_id": meta.get("workspace_id", "default"),
        })

    return retrieved



def retrieve(query, top_k=3, workspace_id=None):
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
        # If workspace_id is provided, filter structured matches if registry has workspace_id
        if workspace_id:
            try:
                from knowledge.document_registry import get_document
                structured_matches = [
                    m for m in structured_matches
                    if (get_document(m["path"]) or {}).get("workspace_id", "default") == workspace_id
                ]
            except Exception:
                pass

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
        # If workspace_id is provided, verify match workspace
        if workspace_id:
            try:
                from knowledge.document_registry import get_document
                doc_record = get_document(file_match["path"])
                if doc_record and doc_record.get("workspace_id", "default") != workspace_id:
                    file_match = None
            except Exception:
                pass

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

    return retrieve_chunks(query, top_k, workspace_id=workspace_id)


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