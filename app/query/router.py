"""
LifeOS unified LLM query router.

Architecture:

    User query
        ↓
    LLM planner
        ↓
    Structured JSON intent
        ↓
    Deterministic Python tool
        ↓
    QueryResult

The LLM decides WHAT the user wants.
Python decides HOW to execute it.

No dataset/document names are hard-coded.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from pathlib import Path
from llm.generator import classify_sources

from retrieval.reranker import rerank


from dotenv import load_dotenv
from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection


from knowledge.registry_api import find_documents
from retrieval.retriever import retrieve, retrieve_chunks
from retrieval.structured_retriever import retrieve_structured_files
from ingestion.structured.query import (
    aggregate_csv,
    count_csv,
    query_csv,
    sort_csv,
)


load_dotenv()


OPENROUTER_API_KEY = os.getenv("OPENROUTER_ROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_ROUTER_MODEL",
    "openai/gpt-oss-120b",
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

REQUEST_TIMEOUT = 30
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
# LLM PLANNER
# ---------------------------------------------------------------------

SYSTEM_PROMPT = """
You are the LifeOS query planner.

Your job is ONLY to understand the user's request and choose the
appropriate LifeOS operation.

You do NOT answer the user.
You do NOT invent information.
You return ONLY valid JSON.

Available operations:

1. file_discovery
   User wants to find, locate, show, send, list, or identify files.

2. document_search
   User wants information contained inside their documents.

3. structured_discovery
   User wants to find or identify a dataset/table/spreadsheet.

4. structured_query
   User wants analysis/calculation/filtering/sorting/counting
   over a structured dataset.

5. schedule_query
   User asks about classes, timetable, schedule, today,
   tomorrow, next class, current class, etc.

6. current_time
   User explicitly asks for current time/date.

7. unknown
   Use when the request cannot reasonably be mapped to the
   available LifeOS capabilities.

IMPORTANT:

"lab" does NOT automatically mean schedule.
"maximum" does NOT automatically mean structured data.
"dataset" does NOT automatically mean analysis.

Use the complete semantic meaning of the request.

Examples:

"I need my Java lab programs"
→ file_discovery

"Which class do I have today?"
→ schedule_query

"What is the maximum stack size?"
→ document_search

"I need my Telco Customer Churn dataset"
→ structured_discovery

"How many customers churned?"
→ structured_query

"What is the average monthly charge?"
→ structured_query

"What time is it?"
→ current_time

Return exactly:

{
  "intent": "one of the available operations",
  "confidence": 0.0,
  "arguments": {}
}

For structured_query, arguments may contain:

{
  "dataset_query": "...",
  "operation": "count | aggregate | filter | sort",
  "column": "...",
  "value": "...",
  "filters": {},
  "aggregation": "sum | avg | min | max",
  "descending": true,
  "limit": 10
}

Only include arguments that are actually required.
"""


def _require_api_key() -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_ROUTER_API_KEY is not configured."
        )

    return OPENROUTER_API_KEY


def _call_planner(query: str) -> dict[str, Any]:
    """
    Ask the LLM for a structured intent.

    Network complexity:
        O(1) request from LifeOS perspective.

    Memory:
        O(response size).
    """

    api_key = _require_api_key()

    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": query,
            },
        ],
        "response_format": {
            "type": "json_object"
        },
    }

    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "LifeOS",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:

            raw = response.read().decode("utf-8")

    except urllib.error.HTTPError as exc:
        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"OpenRouter HTTP {exc.code}: {body}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"OpenRouter connection failed: {exc.reason}"
        ) from exc

    try:
        response_json = json.loads(raw)

        content = response_json["choices"][0]["message"][
            "content"
        ]

        plan = json.loads(content)

    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "OpenRouter returned an invalid planner response."
        ) from exc

    if not isinstance(plan, dict):
        raise RuntimeError(
            "Planner response must be a JSON object."
        )

    return plan


# ---------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------

_ALLOWED_INTENTS = {
    "file_discovery",
    "document_search",
    "structured_discovery",
    "structured_query",
    "schedule_query",
    "current_time",
    "unknown",
}

_ALLOWED_OPERATIONS = {
    "count",
    "aggregate",
    "filter",
    "sort",
}

_ALLOWED_AGGREGATIONS = {
    "sum",
    "avg",
    "min",
    "max",
}


def _validate_plan(plan: dict[str, Any]) -> QueryIntent:

    intent = plan.get("intent")
    confidence = plan.get("confidence", 0.0)
    arguments = plan.get("arguments", {})

    if intent not in _ALLOWED_INTENTS:
        raise ValueError(
            f"Invalid planner intent: {intent!r}"
        )

    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    confidence = max(
        0.0,
        min(1.0, confidence),
    )

    if not isinstance(arguments, dict):
        arguments = {}

    if intent == "structured_query":

        operation = arguments.get("operation")

        if operation not in _ALLOWED_OPERATIONS:
            raise ValueError(
                f"Invalid structured operation: {operation!r}"
            )

        if operation == "aggregate":

            aggregation = arguments.get("aggregation")

            if aggregation not in _ALLOWED_AGGREGATIONS:
                raise ValueError(
                    f"Invalid aggregation: {aggregation!r}"
                )

    return QueryIntent(
        name=intent,
        confidence=confidence,
        arguments=arguments,
    )


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
    """
    Discover files using a two-stage strategy:

    1. Exact registry search.
    2. Semantic document retrieval fallback.

    Exact matches are preferred; semantic matches allow natural-language
    requests such as "my Java lab programs" to find files whose filenames
    do not contain those exact words.
    """

    # Fast path: exact filename/path discovery.
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

    # Semantic fallback.
    retrieved = retrieve(query)

    files = {}
    semantic_scores = {}

    for item in retrieved:
        source = item.get("source")
        file_path = item.get("file_path")

        if not source or not file_path:
            continue

        # Keep the best semantic distance for each file.
        distance = item.get("distance")

        if distance is None:
            distance = float("inf")

        existing = semantic_scores.get(file_path)

        if existing is None or distance < existing:
            semantic_scores[file_path] = distance

            files[file_path] = {
                "path": file_path,
                "filename": source,
                "extension": (
                    Path(source).suffix.lower()
                ),
                "match_type": "semantic",
                "distance": distance,
            }

    results = sorted(
        files.values(),
        key=lambda item: item["distance"],
    )

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "file_discovery",
            0.90 if results else 0.40,
            {"method": "semantic"},
        ),
        answer_type="files",
        data=results,
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
            column=column,
            value=value,
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
    now = datetime.now()

    schedule_context = {
        "current_datetime": now.isoformat(),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
    }

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


# ---------------------------------------------------------------------
# MAIN ROUTER
# ---------------------------------------------------------------------

def route_query(
    query: str,
) -> QueryResult:
    """
    Main LifeOS query entry point.

    Flow:

        query
          ↓
        LLM planner
          ↓
        validation
          ↓
        deterministic execution
          ↓
        QueryResult
    """

    if not isinstance(query, str):
        raise TypeError(
            "query must be a string."
        )

    query = query.strip()

    if not query:
        raise ValueError(
            "Query cannot be empty."
        )

    plan = _call_planner(query)

    intent = _validate_plan(plan)

    if intent.name == "file_discovery":
        return _file_discovery(query)

    if intent.name == "document_search":
        return _document_search(query)

    if intent.name == "structured_discovery":
        return _structured_discovery(query)

    if intent.name == "structured_query":
        return _execute_structured_query(
            query,
            intent.arguments,
        )

    if intent.name == "schedule_query":
        return _schedule_query(query)

    if intent.name == "current_time":
        return _current_time(query)

    return QueryResult(
        query=query,
        intent=intent,
        answer_type="unknown",
        data=None,
    )
