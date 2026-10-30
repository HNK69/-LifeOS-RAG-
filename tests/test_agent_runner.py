from unittest.mock import Mock

from app.agent.decision import AgentDecision
from app.agent.executor import AgentExecutor
from app.agent.runner import AgentRunner
from app.agent.state import AgentState
from app.agent.tools import ToolResult


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