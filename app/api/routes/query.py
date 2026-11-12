"""
query.py

Natural language query endpoint for LifeOS Phase 11.
Dispatches requests to the existing app.main.handle_query engine with full
session and request isolation.
"""

from typing import Any
from fastapi import APIRouter, Depends

from app import main
from app.api.dependencies import (
    RequestContext,
    get_or_create_session,
    get_request_context,
    require_auth,
)
from app.api.schemas import QueryRequest, QueryResponse

router = APIRouter(prefix="/api", tags=["Query"])


def _extract_safe_data(last_result: Any) -> Any:
    """Sanitize and format QueryResult data for safe API exposure."""
    if last_result is None or not hasattr(last_result, "data"):
        return None

    raw_data = last_result.data
    if isinstance(raw_data, list):
        clean_list = []
        for item in raw_data:
            if isinstance(item, dict):
                # Clean file/source item
                clean_item = {
                    "source": item.get("source") or item.get("filename"),
                    "score": item.get("score"),
                }
                if "document" in item and len(str(item["document"])) < 500:
                    clean_item["preview"] = item["document"]
                clean_list.append({k: v for k, v in clean_item.items() if v is not None})
            elif isinstance(item, str):
                clean_list.append(item)
        return clean_list
    elif isinstance(raw_data, dict):
        return {k: v for k, v in raw_data.items() if not k.startswith("_")}
    return None


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Process Natural Language Query",
    description="Processes queries through LifeOS planning, retrieval, reasoning, or agent tools.",
)
async def process_query(
    body: QueryRequest,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    # Support session_id specified via body payload or X-Session-ID header
    active_session_id = body.session_id or ctx.session_id
    if body.session_id and body.session_id != ctx.session_id:
        active_session = get_or_create_session(ctx.user, active_session_id)
    else:
        active_session = ctx.session_data

    # Pass isolated session dictionary to existing core handler
    answer = main.handle_query(
        query=body.query,
        session=active_session,
    )

    last_result = active_session.get("last_result")
    answer_type = getattr(last_result, "answer_type", "unknown") if last_result else "unknown"
    safe_data = _extract_safe_data(last_result)

    return QueryResponse(
        query=body.query,
        answer=answer,
        answer_type=answer_type,
        session_id=active_session_id,
        request_id=ctx.request_id,
        data=safe_data,
    )
