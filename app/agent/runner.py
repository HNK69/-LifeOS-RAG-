from typing import Any

from app.agent.decision import AgentDecision, AgentPlanner
from app.agent.executor import AgentExecutor
from app.agent.state import AgentState
from query.router import QueryResult


class AgentRunner:
    """Run a bounded LifeOS agent execution loop."""

    def __init__(
        self,
        planner: AgentPlanner,
        executor: AgentExecutor,
        max_steps: int = 5,
    ):
        self.planner = planner
        self.executor = executor
        self.max_steps = max_steps

    def run(self, query: str) -> AgentState:
        """Plan and execute a bounded agent loop."""

        state = AgentState(query=query, max_steps=self.max_steps)

        if not query or not query.strip():
            state.mark_failed("Query must not be empty.")
            return state

        state.mark_running()

        try:
            while state.step_count < state.max_steps:
                # Step 0: query only; step > 0: pass query and state
                if state.step_count == 0:
                    decision = self._call_planner(query)
                else:
                    decision = self._call_planner(query, state=state)

                if not isinstance(decision, AgentDecision):
                    state.mark_failed("Planner returned invalid decision type.")
                    return state

                # 1. Terminal finish decision
                if decision.action == "finish":
                    final_ans = (
                        decision.final_answer
                        or self._derive_final_answer(state)
                    )
                    state.mark_completed(final_ans)
                    return state

                # 2. Terminal fail decision
                if decision.action == "fail":
                    state.mark_failed(
                        decision.error or "Agent planning failed."
                    )
                    return state

                # 3. Tool execution decision
                if decision.action == "tool":
                    if not decision.tool_name:
                        state.mark_failed("Tool name required for tool decision.")
                        return state

                    state = self.executor.execute_tool(
                        state=state,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                    )

                    if state.status == "failed":
                        return state

                    # Single-step or terminal tool execution
                    if not decision.has_more_steps:
                        final_ans = (
                            decision.final_answer
                            or self._derive_final_answer(state)
                        )
                        state.mark_completed(final_ans)
                        return state

                    # If has_more_steps is True but reached step limit
                    if state.step_count >= state.max_steps:
                        state.mark_failed("Maximum agent steps exceeded.")
                        return state

            # Exited loop without explicit completion
            if state.status == "running":
                state.mark_failed("Maximum agent steps exceeded.")
            return state

        except Exception as exc:
            state.mark_failed(str(exc))
            return state

    def _call_planner(
        self,
        query: str,
        state: AgentState | None = None,
    ) -> AgentDecision:
        """Invoke planner, passing state if accepted by the planner signature."""
        if state is not None:
            try:
                return self.planner.decide(query, state=state)
            except TypeError:
                return self.planner.decide(query)
        return self.planner.decide(query)

    def _derive_final_answer(self, state: AgentState) -> str | None:
        """Derive grounded summary answer from execution evidence."""
        if not state.tool_calls:
            return None

        last_call = state.tool_calls[-1]
        last_result = last_call.result

        if isinstance(last_result, QueryResult):
            if (
                last_result.answer_type == "time"
                and isinstance(last_result.data, dict)
            ):
                data = last_result.data
                return (
                    f"Today is {data.get('weekday')}, {data.get('date')}. "
                    f"Current time is {data.get('time')}."
                )

            if (
                last_result.answer_type == "files"
                and isinstance(last_result.data, list)
            ):
                count = len(last_result.data)
                return f"Found {count} file{'s' if count != 1 else ''}."

            if (
                last_result.answer_type == "documents"
                and isinstance(last_result.data, list)
            ):
                count = len(last_result.data)
                return (
                    f"Retrieved {count} relevant document chunk"
                    f"{'s' if count != 1 else ''}."
                )

        elif isinstance(last_result, dict):
            return str(last_result)

        return None
