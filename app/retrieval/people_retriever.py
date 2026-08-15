import json

from vectordb.chroma_db import collection


def retrieve_by_person(label, top_k=20):


    """Find indexed images containing a confirmed person label."""

    label = str(label).strip().lower()

    if not label:
        return []

    results = collection.get(
        where={"document_type": "media"},
        include=["documents", "metadatas"],
    )

    matches = []

    for document, metadata in zip(
        results["documents"],
        results["metadatas"],
    ):
        people_raw = metadata.get("people", "[]")

        try:
            people = json.loads(people_raw)
        except (json.JSONDecodeError, TypeError):
            people = []

        for person in people:
            person_label = str(
                person.get("label") or ""
            ).strip().lower()

            if person_label == label:
                matches.append({
                    "document": document,
                    "source": metadata.get("source"),
                    "file_path": metadata.get("file_path"),
                    "person_id": person.get("person_id"),
                    "label": person.get("label"),
                    "similarity": person.get("similarity"),
                    "type": "person",
                })
                break

    return matches[:top_k]

def retrieve_by_people(labels, top_k=20):
    """Find images containing all requested confirmed people."""

    wanted = {
        str(label).strip().lower()
        for label in labels
        if str(label).strip()
    }

    if not wanted:
        return []

    results = collection.get(
        where={"document_type": "media"},
        include=["documents", "metadatas"],
    )

    matches = []

    for document, metadata in zip(
        results["documents"],
        results["metadatas"],
    ):
        try:
            people = json.loads(
                metadata.get("people", "[]")
            )
        except (json.JSONDecodeError, TypeError):
            people = []

        image_labels = {
            str(person.get("label") or "").strip().lower()
            for person in people
        }

        if wanted.issubset(image_labels):
            matches.append({
                "document": document,
                "source": metadata.get("source"),
                "file_path": metadata.get("file_path"),
                "people": people,
                "type": "people",
            })

    return matches[:top_k]