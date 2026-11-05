import pytest
from app.agent.state import AgentState, ToolCall


def test_agent_state_defaults():
    state = AgentState(query="Find my documents about college")

    assert state.status == "pending"
    assert state.tool_calls == []
    assert state.evidence is None
    assert state.final_answer is None
    assert state.error is None
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


def test_agent_state_valid_lifecycle_transitions():
    state = AgentState(query="What time is it?")

    assert state.status == "pending"

    state.mark_running()
    assert state.status == "running"

    state.mark_completed(final_answer="It is 3:00 PM.")
    assert state.status == "completed"
    assert state.final_answer == "It is 3:00 PM."


def test_agent_state_failure_transitions():
    # Transition from pending to failed
    state1 = AgentState(query="bad query")
    state1.mark_failed("Query rejected.")
    assert state1.status == "failed"
    assert state1.error == "Query rejected."

    # Transition from running to failed
    state2 = AgentState(query="failing execution")
    state2.mark_running()
    state2.mark_failed("Tool execution timed out.")
    assert state2.status == "failed"
    assert state2.error == "Tool execution timed out."


def test_agent_state_rejects_invalid_transitions():
    completed_state = AgentState(query="q", status="completed")
    with pytest.raises(ValueError, match="Cannot transition terminal state"):
        completed_state.mark_running()

    failed_state = AgentState(query="q", status="failed")
    with pytest.raises(ValueError, match="Cannot transition terminal state"):
        failed_state.mark_running()

    with pytest.raises(ValueError, match="Cannot transition failed state"):
        failed_state.mark_completed()


def test_agent_state_step_counting_and_custom_max_steps():
    state = AgentState(query="bounded query", max_steps=3)
    assert state.max_steps == 3
    assert state.step_count == 0

    state.step_count += 1
    assert state.step_count == 1
    state.step_count += 2
    assert state.step_count == 3


def test_agent_state_audit_trail_observability():
    state = AgentState(query="audited task", max_steps=2)
    state.mark_running()

    call = ToolCall(
        tool_name="file_discovery",
        arguments={"query": "syllabus"},
        result={"files": ["syllabus.pdf"]},
    )
    state.tool_calls.append(call)
    state.evidence = {
        "query": "audited task",
        "tool_calls": [{"tool_name": "file_discovery", "result": {"files": ["syllabus.pdf"]}}],
    }
    state.step_count = 1
    state.mark_completed(final_answer="Found syllabus.pdf")

    # Verify every audit question can be answered from state:
    assert state.query == "audited task"
    assert state.tool_calls[0].tool_name == "file_discovery"
    assert state.tool_calls[0].arguments == {"query": "syllabus"}
    assert state.tool_calls[0].result == {"files": ["syllabus.pdf"]}
    assert state.step_count == 1
    assert state.max_steps == 2
    assert state.status == "completed"
    assert state.final_answer == "Found syllabus.pdf"
    assert state.evidence is not None
