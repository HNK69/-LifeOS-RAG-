from app.agent.state import AgentState, ToolCall

def test_agent_state_defaults():
    state = AgentState(query="Find my documents about college")

    assert state.status == "pending"
    assert state.tool_calls == []
    assert state.evidence is None
    assert state.final_answer is None
    assert state.step_count == 0
    assert state.max_steps == 5


def test_agent_state_records_tool_call():
    state = AgentState(query="Find my documents")

    state.tool_calls.append(
        ToolCall(
            tool_name="document_search",
            arguments={"query": "Find my documents"},
        )
    )

    assert len(state.tool_calls) == 1
    assert state.tool_calls[0].tool_name == "document_search"