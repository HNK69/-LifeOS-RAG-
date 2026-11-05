from unittest.mock import Mock

from pydantic import BaseModel

from app.agent.decision import AgentDecision
from app.agent.executor import AgentExecutor
from app.agent.runner import AgentRunner
from app.agent.state import AgentState, ToolCall
from app.agent.tools import AgentTool, ToolRegistry, ToolResult
from query.router import QueryIntent, QueryResult


class FakePlanner:
    """Deterministic fake planner for verifying AgentRunner decoupling."""

    def __init__(
        self,
        decision: AgentDecision | None = None,
        decisions: list[AgentDecision] | None = None,
        error: Exception | None = None,
    ):
        self.decision = decision
        self.decisions = list(decisions) if decisions is not None else None
        self.error = error
        self.queries_received: list[str] = []
        self.states_received: list[AgentState | None] = []

    def decide(self, query: str, state: AgentState | None = None) -> AgentDecision:
        self.queries_received.append(query)
        self.states_received.append(state)
        if self.error is not None:
            raise self.error
        if self.decisions:
            return self.decisions.pop(0)
        if self.decision is not None:
            return self.decision
        return AgentDecision(
            tool_name="test_tool",
            arguments={"query": query},
        )


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


def test_runner_multi_step_execution_and_successful_termination():
    registry = ToolRegistry(tools=[EchoTool()])
    executor = AgentExecutor(registry=registry)

    fake_planner = FakePlanner(
        decisions=[
            AgentDecision.call_tool(
                "echo_tool",
                {"message": "step 1 output"},
                has_more_steps=True,
            ),
            AgentDecision.call_tool(
                "echo_tool",
                {"message": "step 2 output"},
                has_more_steps=False,
            ),
        ]
    )

    runner = AgentRunner(planner=fake_planner, executor=executor, max_steps=4)
    state = runner.run("run multi-step task")

    assert state.status == "completed"
    assert state.step_count == 2
    assert len(state.tool_calls) == 2
    assert state.tool_calls[0].arguments == {"message": "step 1 output"}
    assert state.tool_calls[0].result == {"echo": "step 1 output"}
    assert state.tool_calls[1].arguments == {"message": "step 2 output"}
    assert state.tool_calls[1].result == {"echo": "step 2 output"}
    assert state.evidence is not None
    assert len(state.evidence["tool_calls"]) == 2


def test_runner_multi_step_termination_with_finish_action():
    registry = ToolRegistry(tools=[EchoTool()])
    executor = AgentExecutor(registry=registry)

    fake_planner = FakePlanner(
        decisions=[
            AgentDecision.call_tool(
                "echo_tool",
                {"message": "step 1"},
                has_more_steps=True,
            ),
            AgentDecision.finish("Task synthesized completely."),
        ]
    )

    runner = AgentRunner(planner=fake_planner, executor=executor)
    state = runner.run("query with finish")

    assert state.status == "completed"
    assert state.step_count == 1
    assert state.final_answer == "Task synthesized completely."


def test_runner_enforces_max_step_termination():
    registry = ToolRegistry(tools=[EchoTool()])
    executor = AgentExecutor(registry=registry)

    # Infinite tool loop attempt
    fake_planner = FakePlanner(
        decision=AgentDecision.call_tool(
            "echo_tool",
            {"message": "infinite step"},
            has_more_steps=True,
        )
    )

    runner = AgentRunner(planner=fake_planner, executor=executor, max_steps=3)
    state = runner.run("infinite loop query")

    assert state.status == "failed"
    assert state.error == "Maximum agent steps exceeded."
    assert state.step_count == 3
    assert len(state.tool_calls) == 3


def test_runner_handles_planner_failure_during_multi_step():
    registry = ToolRegistry(tools=[EchoTool()])
    executor = AgentExecutor(registry=registry)

    class StepPlanner:
        def __init__(self):
            self.calls = 0

        def decide(self, query: str, state: AgentState | None = None) -> AgentDecision:
            self.calls += 1
            if self.calls == 1:
                return AgentDecision.call_tool(
                    "echo_tool",
                    {"message": "first step"},
                    has_more_steps=True,
                )
            raise RuntimeError("Planner died on step 2.")

    runner = AgentRunner(planner=StepPlanner(), executor=executor)
    state = runner.run("fail on second step")

    assert state.status == "failed"
    assert "Planner died on step 2" in state.error
    assert state.step_count == 1
    assert len(state.tool_calls) == 1


def test_runner_handles_invalid_decision_type():
    mock_planner = Mock()
    mock_planner.decide.return_value = "invalid_string_instead_of_decision"
    mock_executor = Mock()

    runner = AgentRunner(planner=mock_planner, executor=mock_executor)
    state = runner.run("malformed decision")

    assert state.status == "failed"
    assert state.error == "Planner returned invalid decision type."


def test_runner_handles_missing_tool_name_in_tool_decision():
    mock_planner = Mock()
    mock_planner.decide.return_value = AgentDecision(action="tool", tool_name=None)
    mock_executor = Mock()

    runner = AgentRunner(planner=mock_planner, executor=mock_executor)
    state = runner.run("missing tool name")

    assert state.status == "failed"
    assert state.error == "Tool name required for tool decision."


def test_runner_derives_grounded_final_answer_from_query_result():
    registry = ToolRegistry(tools=[EchoTool()])

    class QueryResultTool(AgentTool):
        name = "qr_tool"
        description = "Returns QueryResult"
        input_schema = EchoInput

        def execute(self, input_data: EchoInput) -> ToolResult:
            qr = QueryResult(
                query="what time is it?",
                intent=QueryIntent("current_time", 1.0, {}),
                answer_type="time",
                data={"weekday": "Thursday", "date": "Nov 5, 2026", "time": "11:30 PM"},
            )
            return ToolResult(success=True, data=qr)

    registry = ToolRegistry(tools=[QueryResultTool()])
    executor = AgentExecutor(registry=registry)

    fake_planner = FakePlanner(
        decision=AgentDecision.call_tool(
            "qr_tool",
            {"message": "get time"},
            has_more_steps=False,
        )
    )

    runner = AgentRunner(planner=fake_planner, executor=executor)
    state = runner.run("what time is it?")

    assert state.status == "completed"
    assert state.final_answer == "Today is Thursday, Nov 5, 2026. Current time is 11:30 PM."
