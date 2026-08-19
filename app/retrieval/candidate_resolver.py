

def resolve_candidates(query, candidates, top_k=20):
    """
    Rank heterogeneous LifeOS candidates using available evidence.

    Does not assume a specific modality or query type.
    """
    if not candidates:
        return []

    query_terms = {
        term.lower()
        for term in str(query).replace("/", " ").split()
        if len(term) > 2
    }

    scored = []

    for candidate in candidates:
        item = dict(candidate)

        text = str(item.get("document") or "").lower()
        source = str(item.get("source") or "").lower()
        metadata = item.get("metadata") or {}

        searchable = " ".join(
            [
                text,
                source,
                str(metadata.get("description") or ""),
                str(metadata.get("people") or ""),
            ]
        ).lower()

        lexical_score = sum(
            1 for term in query_terms if term in searchable
        )

        distance = item.get("distance")
        if distance is None:
            semantic_score = 0.0
        else:
            semantic_score = 1.0 / (1.0 + float(distance))

        score = (
            semantic_score * 5.0
            + lexical_score * 2.0
        )

        item["resolution_score"] = score
        scored.append(item)

    scored.sort(
        key=lambda item: item["resolution_score"],
        reverse=True,
    )

    return scored[:top_k]