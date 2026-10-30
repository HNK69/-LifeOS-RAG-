from app.agent.decision import AgentPlanner
from app.agent.executor import AgentExecutor
from app.agent.state import AgentState


class AgentRunner:
    """Run one bounded LifeOS agent execution."""

    def __init__(
        self,
        planner: AgentPlanner,
        executor: AgentExecutor,
    ):
        self.planner = planner
        self.executor = executor

    def run(self, query: str) -> AgentState:
        """Plan and execute one bounded agent step."""

        state = AgentState(query=query)

        if not query or not query.strip():
            state.status = "failed"
            state.error = "Query must not be empty."
            return state

        try:
            decision = self.planner.decide(query)

            state = self.executor.execute_tool(
                state=state,
                tool_name=decision.tool_name,
                arguments=decision.arguments,
            )

            if state.status == "running":
                state.status = "completed"

            return state

        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
            return state