"""
reranker.py

Ranks retrieved documents using multiple signals.

Signals:
- semantic similarity
- filename relevance
- content relevance

No hardcoded document types.
"""


def rerank(
    query,
    documents,
):
    """
    Reorder retrieved documents by relevance.

    Input:
        query: user question
        documents: retrieval results

    Output:
        sorted documents
    """

    if not documents:
        return []

    query_words = {
        word.lower()
        for word in query.replace("/", " ").split()
        if len(word) > 2
    }

    scored = []

    for item in documents:

        text = str(
            item.get("document", "")
        ).lower()

        source = str(
            item.get("source", "")
        ).lower()

        distance = item.get(
            "distance",
            1.0,
        )

        filename_words = {
            word
            for word in source
            .replace("_", " ")
            .replace("-", " ")
            .split()
            if len(word) > 2
        }

        content_score = sum(
            1
            for word in query_words
            if word in text
        )

        filename_score = len(
            query_words & filename_words
        )

        semantic_score = 1 / (
            1 + distance
        )

        score = (
            content_score * 2
            + filename_score * 3
            + semantic_score * 5
        )

        item["rerank_score"] = score

        scored.append(item)

    return sorted(
        scored,
        key=lambda x: x["rerank_score"],
        reverse=True,
    )