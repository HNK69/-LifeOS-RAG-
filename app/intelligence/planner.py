import json
from typing import Any

from llm.generator import generate_response

from .models import IntentPlan


SYSTEM_PROMPT = """
You are the LifeOS Intelligence Planner.

Understand the user's request and return ONLY valid JSON.
Classify by the complete semantic intent, not isolated keywords.

Available intents:
- file_discovery: find/list/locate/show a file or document itself
- document_search: answer/explain/retrieve information from document contents
- document_search: answer using document contents
  Use this for explanations and factual questions whose answer should come from documents.
- structured_discovery: find a dataset/table/spreadsheet
- structured_query: calculate/filter/sort/count/aggregate a dataset
  Average, mean, maximum, minimum, total, and "how many" analytical requests belong here.
  The dataset name does not have to be explicitly stated; infer the subject when possible.
- schedule_query: ask about timetable/classes/schedule
- current_time: explicitly ask current time/date
- unknown: unsupported request

Semantic rules:
- Do not classify from isolated keywords.
- "lab" is not automatically schedule_query.
- "maximum" is not automatically structured_query.
- "dataset" is not automatically structured_query.
- Use the complete meaning of the request.

Return:
{
  "intent": "...",
  "confidence": 0.0,
  "arguments": {}
}

For structured_query, ALWAYS provide operation. If operation is aggregate, ALWAYS provide aggregation (sum, avg, min, or max). Arguments may contain:
dataset_query, operation, column, value, filters,
aggregation, descending, limit.

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

    # Normalize incomplete structured-query plans.
    if data.get("intent") == "structured_query":
        arguments = data.setdefault("arguments", {})

        operation = arguments.get("operation")
        if operation in {"how_many", "number", "count_rows"}:
            arguments["operation"] = "count"

        # Natural-language counting requests are row-count operations.
        if not arguments.get("operation"):
            query_lower = query.lower()
            count_phrases = (
                "how many",
                "number of",
                "count of",
                "count the",
            )
            if any(phrase in query_lower for phrase in count_phrases):
                arguments["operation"] = "count"

        if arguments.get("operation") == "count":
            arguments.pop("aggregation", None)

    return IntentPlan.model_validate(data)
