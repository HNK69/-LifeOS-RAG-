"""
LifeOS query tool functions.

These functions are the deterministic execution layer called by the
intelligence router after the LLM planner classifies the user's intent.

Architecture:

    User query
        ↓
    intelligence/planner.py  (LLM → IntentPlan)
        ↓
    intelligence/router.py   (dispatch)
        ↓
    query/router.py          (deterministic tool functions)
        ↓
    QueryResult

The LLM decides WHAT the user wants.
Python decides HOW to execute it.

No dataset/document names are hard-coded.
"""

from __future__ import annotations


import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from pathlib import Path
from llm.generator import classify_sources

from retrieval.reranker import rerank


from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection

from context.context_store import get_relevant_context
from context.context_store import compose_context


from knowledge.registry_api import find_documents
from retrieval.retriever import retrieve, retrieve_chunks
from retrieval.structured_retriever import retrieve_structured_files
from ingestion.structured.query import (
    aggregate_csv,
    count_csv,
    query_csv,
    sort_csv,
)


MAX_RESULTS = 10


@dataclass(frozen=True)
class QueryIntent:
    name: str
    confidence: float
    arguments: dict[str, Any]

@dataclass
class QueryResult:
    query: str
    intent: Any
    answer_type: str
    data: Any


# ---------------------------------------------------------------------
# FILE DISCOVERY
# ---------------------------------------------------------------------

def _file_discovery(query: str) -> QueryResult:
    """
    Hybrid file discovery.

    Combines:
      1. Exact registry matches
      2. File-index matches
      3. Semantic retrieval
      4. Filename/content relevance

    Returns ranked candidate files rather than trusting one embedding.
    """

    # ---------------------------------------------------------
    # 1. Exact registry lookup
    # ---------------------------------------------------------

    exact_matches = find_documents(query)

    if exact_matches:
        return QueryResult(
            query=query,
            intent=QueryIntent(
                "file_discovery",
                1.0,
                {"method": "registry"},
            ),
            answer_type="files",
            data=exact_matches,
        )

    # ---------------------------------------------------------
    # 2. Semantic retrieval with a larger candidate pool
    # ---------------------------------------------------------

    retrieved = retrieve(query, top_k=10)

    query_words = {
        word.lower()
        for word in query.replace("/", " ").split()
        if len(word) > 2
    }

    candidates = {}

    for item in retrieved:

        source = item.get("source")
        file_path = item.get("file_path")

        if not source or not file_path:
            continue

        filename = Path(source).stem.lower()

        # Filename token overlap.
        filename_words = {
            word
            for word in filename.replace("_", " ")
            .replace("-", " ")
            .split()
            if len(word) > 2
        }

        filename_overlap = len(
            query_words & filename_words
        )

        # Content token overlap.
        document = str(
            item.get("document") or ""
        ).lower()

        content_overlap = sum(
            1
            for word in query_words
            if word in document
        )

        distance = item.get(
            "distance",
            float("inf"),
        )

        # Lower distance is better.
        #
        # Filename matches are deliberately weighted strongly
        # because this operation is FILE discovery, not Q&A.
        semantic_score = 0.0

        if distance != float("inf"):
            semantic_score = 1 / (1 + distance)

        score = (
            filename_overlap * 4.0
            + content_overlap * 2.0
            + semantic_score * 5.0
        )

        # Semantic similarity alone cannot establish file relevance.
        # Require lexical evidence from the filename or content.
        if filename_overlap == 0 and content_overlap == 0:
            continue

        existing = candidates.get(file_path)

        candidate = {
            "path": file_path,
            "filename": source,
            "extension": Path(source).suffix.lower(),
            "match_type": "semantic",
            "distance": distance,
            "filename_overlap": filename_overlap,
            "content_overlap": content_overlap,
            "score": score,
        }

        if (
            existing is None
            or score > existing["score"]
        ):
            candidates[file_path] = candidate

    # ---------------------------------------------------------
    # 3. Rank candidates
    # ---------------------------------------------------------

    results = sorted(
        candidates.values(),
        key=lambda item: item["score"],
        reverse=True,
    )

    # Don't expose internal scoring details to the UI.
    cleaned_results = []

    for item in results[:5]:
        cleaned_results.append(
            {
                "path": item["path"],
                "filename": item["filename"],
                "extension": item["extension"],
                "match_type": item["match_type"],
                "distance": item["distance"],
            }
        )

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "file_discovery",
            0.90 if cleaned_results else 0.40,
            {
                "method": "hybrid",
                "candidate_count": len(
                    cleaned_results
                ),
            },
        ),
        answer_type="files",
        data=cleaned_results,
    )


# ---------------------------------------------------------------------
# DOCUMENT SEARCH
# ---------------------------------------------------------------------

def _document_search(query: str) -> QueryResult:
    """Retrieve actual document chunks for document Q&A."""

    retrieved = retrieve_chunks(
        query,
        top_k=10,
    )
    
    retrieved = rerank(
        query,
        retrieved,
    )

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "document_search",
            1.0,
            {
                "method": "semantic_chunks",
            },
        ),
        answer_type="documents",
        data=retrieved,
    )


# ---------------------------------------------------------------------
# STRUCTURED FILE DISCOVERY
# ---------------------------------------------------------------------

def _structured_discovery(
    query: str,
) -> QueryResult:

    results = retrieve_structured_files(query)

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "structured_discovery",
            1.0,
            {},
        ),
        answer_type="structured_files",
        data=results,
    )


# ---------------------------------------------------------------------
# STRUCTURED QUERY
# ---------------------------------------------------------------------

def _find_dataset(dataset_query: str) -> dict[str, Any]:
    """Resolve a structured dataset using filename and schema evidence."""

    candidates = retrieve_structured_files()

    if not candidates:
        raise FileNotFoundError("No structured datasets are available.")

    query_words = {
        word
        for word in (
            dataset_query.lower()
            .replace("-", " ")
            .replace("_", " ")
            .split()
        )
        if len(word) > 2
    }

    if not query_words:
        raise FileNotFoundError(
            f"No structured dataset found for: {dataset_query}"
        )

    scored = []

    for dataset in candidates:
        filename = Path(dataset["filename"]).stem.lower()

        filename_words = {
            word
            for word in (
                filename
                .replace("-", " ")
                .replace("_", " ")
                .split()
            )
            if len(word) > 2
        }

        schema_words = set()

        if dataset["extension"] == ".csv":
            import csv

            try:
                with open(
                    dataset["path"],
                    "r",
                    encoding="utf-8-sig",
                    newline="",
                ) as file:
                    reader = csv.reader(file)
                    headers = next(reader, [])

                for header in headers:
                    schema_words.update(
                        word
                        for word in (
                            header.lower()
                            .replace("-", " ")
                            .replace("_", " ")
                            .split()
                        )
                        if len(word) > 2
                    )
            except (OSError, csv.Error):
                continue

        filename_overlap = len(
            query_words & filename_words
        )

        schema_overlap = len(
            query_words & schema_words
        )

        score = (
            filename_overlap * 3.0
            + schema_overlap * 2.0
        )

        if score > 0:
            scored.append((score, dataset))

    if not scored:
        raise FileNotFoundError(
            f"No structured dataset found for: {dataset_query}"
        )

    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    best_score, best_dataset = scored[0]

    if len(scored) > 1 and best_score == scored[1][0]:
        raise ValueError(
            f"Ambiguous structured dataset query: {dataset_query}"
        )

    return best_dataset

def _execute_structured_query(
    query: str,
    arguments: dict[str, Any],
) -> QueryResult:

    dataset_query = arguments.get(
        "dataset_query",
        query,
    )

    dataset = _find_dataset(dataset_query)

    file_path = dataset["path"]

    operation = arguments["operation"]

    if operation == "count":

        filters = arguments.get(
            "filters",
            {},
        )

        result = count_csv(
            file_path,
            filters,
        )

    elif operation == "aggregate":

        column = arguments.get("column")

        if not column:
            raise ValueError(
                "Aggregate query requires a column."
            )

        aggregation = arguments["aggregation"]

        result = aggregate_csv(
            file_path,
            column,
            aggregation,
        )

    elif operation == "filter":

        column = arguments.get("column")
        value = arguments.get("value")

        if not column:
            raise ValueError(
                "Filter query requires a column."
            )

        result = query_csv(
            file_path,
            filters={column: value},
            max_results=min(
                int(
                    arguments.get(
                        "limit",
                        MAX_RESULTS,
                    )
                ),
                MAX_RESULTS,
            ),
        )

    elif operation == "sort":

        column = arguments.get("column")

        if not column:
            raise ValueError(
                "Sort query requires a column."
            )

        result = sort_csv(
            file_path,
            column,
            descending=bool(
                arguments.get(
                    "descending",
                    False,
                )
            ),
            filters=arguments.get(
                "filters",
                {},
            ),
            max_results=min(
                int(
                    arguments.get(
                        "limit",
                        MAX_RESULTS,
                    )
                ),
                MAX_RESULTS,
            ),
        )

    else:
        raise ValueError(
            f"Unsupported operation: {operation}"
        )

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "structured_query",
            1.0,
            arguments,
        ),
        answer_type="structured_result",
        data={
            "dataset": dataset,
            "result": result,
        },
    )


# ---------------------------------------------------------------------
# SCHEDULE
# ---------------------------------------------------------------------

def _schedule_query(query: str) -> QueryResult:
    schedule_context = _build_schedule_context(query)

    retrieval_query = (
        f"{query}\n"
        f"Date: {schedule_context['date']}\n"
        f"Weekday: {schedule_context['weekday']}\n"
        f"Time: {schedule_context['time']}"
    )

    chunks = retrieve_chunks(retrieval_query, top_k=15)

    # Group chunks by physical source.
    grouped = {}

    for item in chunks:
        path = item.get("file_path")
        if path:
            grouped.setdefault(path, []).append(item)

    # Build source candidates for the LLM.
    candidates = []

    for index, (path, items) in enumerate(grouped.items()):
        candidates.append({
            "index": index,
            "source": items[0].get("source"),
            "file_path": path,
            "content": "\n\n".join(
                item["document"] for item in items
            ),
        })

    if not candidates:
        selected_sources = []
    else:
        raw_selection = classify_sources(
            query,
            schedule_context,
            candidates,
        )

        try:
            selection = json.loads(raw_selection)
            scored = selection.get("candidates", [])

            scored = sorted(
                scored,
                key=lambda item: float(item.get("relevance", 0)),
                reverse=True,
            )

            selected_sources = {
                int(item["index"])
                for item in scored[:3]
                if 0 <= int(item["index"]) < len(candidates)
                and float(item.get("relevance", 0)) >= 60
            }

        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            selected_sources = set()

    # If the classifier returns nothing, fail closed.
    documents = []

    for index, (_, items) in enumerate(grouped.items()):
        if index not in selected_sources:
            continue

        documents.extend(
            sorted(
                items,
                key=lambda item: item["distance"],
            )
        )

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "schedule_query",
            1.0,
            schedule_context,
        ),
        answer_type="schedule_context",
        data={
            "time_context": schedule_context,
            "documents": documents,
        },
    )

# ---------------------------------------------------------------------
# CURRENT TIME
# ---------------------------------------------------------------------

def _current_time(
    query: str,
) -> QueryResult:

    now = datetime.now()

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "current_time",
            1.0,
            {},
        ),
        answer_type="time",
        data={
            "datetime": now.isoformat(),
            "date": now.date().isoformat(),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
        },
    )

def _build_schedule_context(query):
    now = datetime.now()

    context = {
        "current_datetime": now.isoformat(),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
    }

    context["personal_context"] = compose_context(
        query,
        datetime.now().astimezone(),
    )

    return context