from query.router import QueryResult

from app.agent.state import AgentState


def agent_state_to_query_result(state: AgentState) -> QueryResult:
    """Convert a completed agent execution into the existing QueryResult contract."""

    if state.status != "completed":
        raise ValueError("Only completed agent executions can be converted.")

    if not state.tool_calls:
        raise ValueError("Agent execution contains no tool calls.")

    tool_call = state.tool_calls[-1]

    if tool_call.error:
        raise ValueError(tool_call.error)

    if not isinstance(tool_call.result, QueryResult):
        raise ValueError("Agent tool result is not a QueryResult.")

    return tool_call.result