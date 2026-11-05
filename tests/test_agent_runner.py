from unittest.mock import Mock

from pydantic import BaseModel

from app.agent.decision import AgentDecision
from app.agent.executor import AgentExecutor
from app.agent.runner import AgentRunner
from app.agent.state import AgentState, ToolCall
from app.agent.tools import AgentTool, ToolRegistry, ToolResult


class FakePlanner:
    """Deterministic fake planner for verifying AgentRunner decoupling."""

    def __init__(
        self,
        decision: AgentDecision | None = None,
        error: Exception | None = None,
    ):
        self.decision = decision
        self.error = error
        self.queries_received: list[str] = []

    def decide(self, query: str) -> AgentDecision:
        self.queries_received.append(query)
        if self.error is not None:
            raise self.error
        if self.decision is not None:
            return self.decision
        return AgentDecision(
            tool_name="test_tool",
            arguments={"query": query},
        )


def test_agent_runner_completes_successful_execution():
    planner = Mock()
    planner.decide.return_value = AgentDecision(
        tool_name="document_search",
        arguments={"query": "college notes"},
    )

    executor = Mock()
    executor.execute_tool.return_value = AgentState(
        query="college notes",
        status="running",
        step_count=1,
    )

    runner = AgentRunner(planner, executor)

    result = runner.run("college notes")

    assert result.status == "completed"
    assert result.step_count == 1

    planner.decide.assert_called_once_with("college notes")
    executor.execute_tool.assert_called_once()


def test_agent_runner_propagates_execution_failure():
    planner = Mock()
    planner.decide.return_value = AgentDecision(
        tool_name="document_search",
        arguments={"query": "college notes"},
    )

    executor = Mock()
    executor.execute_tool.return_value = AgentState(
        query="college notes",
        status="failed",
        error="Search failed.",
        step_count=1,
    )

    runner = AgentRunner(planner, executor)

    result = runner.run("college notes")

    assert result.status == "failed"
    assert result.error == "Search failed."


def test_agent_runner_rejects_empty_query():
    planner = Mock()
    executor = Mock()

    runner = AgentRunner(planner, executor)

    result = runner.run("   ")

    assert result.status == "failed"
    assert result.error == "Query must not be empty."

    planner.decide.assert_not_called()
    executor.execute_tool.assert_not_called()


def test_runner_accepts_injected_fake_planner_and_passes_decision_to_executor():
    decision = AgentDecision(
        tool_name="deterministic_search",
        arguments={"filter": "recent", "limit": 5},
    )
    fake_planner = FakePlanner(decision=decision)

    mock_executor = Mock()
    mock_executor.execute_tool.return_value = AgentState(
        query="find recent files",
        status="running",
        step_count=1,
    )

    runner = AgentRunner(planner=fake_planner, executor=mock_executor)
    result = runner.run("find recent files")

    assert fake_planner.queries_received == ["find recent files"]
    mock_executor.execute_tool.assert_called_once()
    call_kwargs = mock_executor.execute_tool.call_args.kwargs
    assert call_kwargs["tool_name"] == "deterministic_search"
    assert call_kwargs["arguments"] == {"filter": "recent", "limit": 5}
    assert result.status == "completed"


def test_runner_with_fake_planner_and_executor_produces_completed_state_with_tool_call():
    class EchoInput(BaseModel):
        message: str

    class EchoTool(AgentTool):
        name = "echo_tool"
        description = "Deterministic echo test tool"
        input_schema = EchoInput

        def execute(self, input_data: EchoInput) -> ToolResult:
            return ToolResult(
                success=True,
                data={"echo": input_data.message},
            )

    registry = ToolRegistry(tools=[EchoTool()])
    executor = AgentExecutor(registry=registry)

    fake_planner = FakePlanner(
        decision=AgentDecision(
            tool_name="echo_tool",
            arguments={"message": "hello lifeos"},
        )
    )

    runner = AgentRunner(planner=fake_planner, executor=executor)
    state = runner.run("echo hello lifeos")

    assert state.status == "completed"
    assert state.step_count == 1
    assert state.error is None
    assert len(state.tool_calls) == 1

    tool_call = state.tool_calls[0]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.tool_name == "echo_tool"
    assert tool_call.arguments == {"message": "hello lifeos"}
    assert tool_call.result == {"echo": "hello lifeos"}
    assert tool_call.error is None


def test_runner_with_fake_planner_handles_planner_failure():
    fake_planner = FakePlanner(
        error=RuntimeError("Deterministic planner failure triggered.")
    )
    mock_executor = Mock()

    runner = AgentRunner(planner=fake_planner, executor=mock_executor)
    state = runner.run("trigger planner failure")

    assert state.status == "failed"
    assert state.error == "Deterministic planner failure triggered."
    assert fake_planner.queries_received == ["trigger planner failure"]
    mock_executor.execute_tool.assert_not_called()


def test_runner_with_fake_planner_rejects_empty_query_without_invoking_planner():
    fake_planner = FakePlanner(
        decision=AgentDecision(tool_name="test_tool", arguments={})
    )
    mock_executor = Mock()

    runner = AgentRunner(planner=fake_planner, executor=mock_executor)

    for empty_query in ["", "   ", "\t\n"]:
        state = runner.run(empty_query)

        assert state.status == "failed"
        assert state.error == "Query must not be empty."

    assert fake_planner.queries_received == []
    mock_executor.execute_tool.assert_not_called()
