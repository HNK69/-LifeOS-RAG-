from query.router import QueryResult

from app.agent.state import AgentState


def agent_state_to_query_result(state: AgentState) -> QueryResult:
    """Convert a completed agent execution into the existing QueryResult contract."""

    if state.status != "completed":
        raise ValueError(
            "Only completed agent executions can be converted."
        )

    if not state.tool_calls:
        raise ValueError(
            "Agent execution contains no tool calls."
        )

    tool_call = state.tool_calls[-1]

    if tool_call.error:
        raise ValueError(tool_call.error)

    return QueryResult(
        query=state.query,
        intent=tool_call.tool_name,
        answer_type="documents",
        data=tool_call.result,
    )