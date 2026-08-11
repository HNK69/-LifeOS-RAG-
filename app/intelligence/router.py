from query.router import (
    QueryResult,
    _file_discovery,
    _document_search,
    _structured_discovery,
    _execute_structured_query,
    _schedule_query,
    _current_time,
)

from .models import IntentPlan
from .planner import plan_query


def execute_plan(query: str, plan: IntentPlan) -> QueryResult:
    intent = plan.intent
    args = plan.arguments

    if intent == "file_discovery":
        return _file_discovery(query)

    if intent == "document_search":
        return _document_search(query)

    if intent == "structured_discovery":
        return _structured_discovery(query)

    if intent == "structured_query":
        data = args.model_dump(exclude_none=True)

        if not data.get("dataset_query"):
            data["dataset_query"] = query

        return _execute_structured_query(query, data)

    if intent == "schedule_query":
        return _schedule_query(query)

    if intent == "current_time":
        return _current_time(query)

    return QueryResult(
        query=query,
        intent=plan,
        answer_type="unknown",
        data=None,
    )


def route_intelligent(query: str) -> QueryResult:
    plan = plan_query(query)
    return execute_plan(query, plan)
