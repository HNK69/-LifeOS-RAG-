import json
import re
from typing import Any

from llm.generator import generate_response

from .models import IntentPlan


# ---------------------------------------------------------------------
# DETERMINISTIC STRUCTURED-QUERY RESCUE
# ---------------------------------------------------------------------
#
# The LLM planner is non-deterministic.  On some runs it correctly
# classifies "What is the maximum monthly charge?" as structured_query;
# on others it returns "unknown".
#
# This rescue layer runs AFTER the LLM and checks whether the raw user
# query contains clear structured-data aggregation/count signals.
# When it detects them AND the LLM returned "unknown", it overrides
# the intent to structured_query with the correct operation and
# aggregation.
#
# Design constraints:
#   - No test-specific strings or hardcoded dataset/column names.
#   - Only generic aggregation/count patterns.
#   - Must remain extensible for future structured intents.
# ---------------------------------------------------------------------

# Compiled once.  Each tuple: (regex, operation, aggregation-or-None).
_STRUCTURED_PATTERNS: list[tuple[re.Pattern, str, str | None]] = [
    (re.compile(r"\b(?:maximum|highest|largest|greatest|max)\b", re.I),
     "aggregate", "max"),
    (re.compile(r"\b(?:minimum|lowest|smallest|least|min)\b", re.I),
     "aggregate", "min"),
    (re.compile(r"\b(?:average|mean|avg)\b", re.I),
     "aggregate", "avg"),
    (re.compile(r"\b(?:total|sum)\b", re.I),
     "aggregate", "sum"),
    (re.compile(r"\b(?:how many|count of|count the|number of)\b", re.I),
     "count", None),
    (re.compile(r"\b(?:sort|order|rank)\b", re.I),
     "sort", None),
    (re.compile(r"\b(?:filter|where|with)\b.*\b(?:equal|equals|is)\b", re.I),
     "filter", None),
]


def _rescue_structured_intent(
    query: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """
    Override 'unknown' intent when the query clearly requests a
    structured-data operation.

    Only activates when:
      1. The LLM returned intent == "unknown".
      2. A generic aggregation/count pattern is found in the query.

    Returns the (possibly mutated) data dict.
    """

    if data.get("intent") != "unknown":
        return data

    for pattern, operation, aggregation in _STRUCTURED_PATTERNS:
        if pattern.search(query):
            data["intent"] = "structured_query"
            arguments = data.setdefault("arguments", {})
            arguments["operation"] = operation

            if aggregation is not None:
                arguments["aggregation"] = aggregation

            # Use the full query as the dataset_query fallback so
            # the downstream dataset resolver can attempt matching.
            if not arguments.get("dataset_query"):
                arguments["dataset_query"] = query

            break

    return data


SYSTEM_PROMPT = """
You are the LifeOS Intelligence Planner.

Understand the user's request and return ONLY valid JSON.
Classify by the complete semantic intent, not isolated keywords.

Available intents:
- file_discovery: find/list/locate/show a file or document itself
- document_search: answer/explain/retrieve information from document contents
- structured_discovery: find a dataset/table/spreadsheet
- structured_query: calculate/filter/sort/count/aggregate a dataset
- schedule_query: ask about timetable/classes/schedule
- current_time: explicitly ask current time/date
- unknown: unsupported request

Semantic rules:
- Do not classify from isolated keywords.
- "lab" is not automatically schedule_query.
- "maximum" is not automatically structured_query.
- "dataset" is not automatically structured_query.
- Use the complete meaning of the request.

structured_discovery:
Use when the user wants to find, list, locate, or identify datasets,
tables, spreadsheets, CSV files, JSON datasets, or other structured data.
If the request explicitly asks to show/list datasets or structured files,
prefer structured_discovery over file_discovery.

document_search:
Use for technical, academic, educational, or conceptual questions
that LifeOS may reasonably retrieve from stored knowledge/documents.

This includes standalone conceptual questions even when the user does
not explicitly say "my documents".

Do NOT use document_search for casual requests, cooking instructions,
jokes, poems, general conversation, or unsupported requests.

Standalone general-world-knowledge questions such as geography, trivia,
or common facts unrelated to technical/academic knowledge retrieval
should be classified as unknown.

structured_query:
Use when the user asks to calculate, count, aggregate, filter, sort,
or find a numeric/statistical property of structured data.

Requests involving maximum, minimum, average, mean, total, sum, count,
how many, highest, or lowest should be structured_query when the subject
can reasonably represent a dataset field or structured data.

Priority rule:
When a request can reasonably be interpreted as a structured-data
calculation or aggregation, structured_query takes precedence over
document_search.

For structured_query, ALWAYS provide operation.
If operation is aggregate, ALWAYS provide aggregation
using one of: sum, avg, min, max.

Arguments may contain:
dataset_query, operation, column, value, filters,
aggregation, descending, limit.

people_search:
Use when the user is asking to find, show, or retrieve media based on
people/entities appearing in that media.

Extract the referenced person/entity names or labels into person_labels.
Do not assume a fixed set of names or keywords.
If multiple people are referenced, include all of them.

Return:
{
  "intent": "...",
  "confidence": 0.0,
  "arguments": {}
}

Do not answer the user.
Do not invent facts.
"""


def plan_query(query: str) -> IntentPlan:
    raw = generate_response(
        SYSTEM_PROMPT
        + "\n\nUSER QUERY:\n"
        + query
    )

    data: dict[str, Any] = json.loads(raw)

    # ------------------------------------------------------------------
    # Deterministic rescue: if the LLM returned "unknown" but the
    # query clearly contains structured-data signals, override.
    # ------------------------------------------------------------------
    data = _rescue_structured_intent(query, data)

    if data.get("intent") == "structured_query":
        arguments = data.setdefault("arguments", {})

        operation = str(
            arguments.get("operation") or ""
        ).lower().strip()

        operation_aliases = {
            "how_many": "count",
            "number": "count",
            "count_rows": "count",
            "sum": "aggregate",
            "total": "aggregate",
            "avg": "aggregate",
            "average": "aggregate",
            "mean": "aggregate",
            "min": "aggregate",
            "minimum": "aggregate",
            "max": "aggregate",
            "maximum": "aggregate",
        }

        aggregation_aliases = {
            "sum": "sum",
            "total": "sum",
            "avg": "avg",
            "average": "avg",
            "mean": "avg",
            "min": "min",
            "minimum": "min",
            "max": "max",
            "maximum": "max",
        }

        if operation in operation_aliases:
            normalized_operation = operation_aliases[operation]
            arguments["operation"] = normalized_operation

            if normalized_operation == "aggregate":
                arguments["aggregation"] = aggregation_aliases[
                    operation
                ]

        aggregation = str(
            arguments.get("aggregation") or ""
        ).lower().strip()

        if aggregation in aggregation_aliases:
            arguments["aggregation"] = aggregation_aliases[
                aggregation
            ]

        if not arguments.get("operation"):
            query_lower = query.lower()

            if any(
                phrase in query_lower
                for phrase in (
                    "how many",
                    "number of",
                    "count of",
                    "count the",
                )
            ):
                arguments["operation"] = "count"

        if arguments.get("operation") == "count":
            arguments.pop("aggregation", None)

    return IntentPlan.model_validate(data)