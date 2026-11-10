from typing import Any, Literal

from pydantic import BaseModel, Field

from intelligence.planner import plan_query
from app.agent.state import AgentState
from app.agent.tools import ToolRegistry

ActionType = Literal["tool", "finish", "fail"]


class AgentDecision(BaseModel):
    """Validated decision describing an agent action."""

    action: ActionType = "tool"
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    has_more_steps: bool = False
    final_answer: str | None = None
    error: str | None = None

    @classmethod
    def call_tool(
        cls,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        has_more_steps: bool = False,
    ) -> "AgentDecision":
        """Convenience constructor for a tool execution step."""
        return cls(
            action="tool",
            tool_name=tool_name,
            arguments=arguments or {},
            has_more_steps=has_more_steps,
        )

    @classmethod
    def finish(cls, final_answer: str | None = None) -> "AgentDecision":
        """Convenience constructor for successful termination."""
        return cls(
            action="finish",
            final_answer=final_answer,
        )

    @classmethod
    def fail(cls, error: str) -> "AgentDecision":
        """Convenience constructor for safe failure."""
        return cls(
            action="fail",
            error=error,
        )


class AgentPlanner:
    """Translate the existing LifeOS intent planner into an agent decision."""

    INTENT_TO_TOOL = {
        "current_time": "current_time",
        "document_search": "document_search",
        "file_discovery": "file_discovery",
        "relationship_search": "relationship_search",
    }

    @classmethod
    def supports_intent(cls, intent: str | None) -> bool:
        """Check if an intent can be handled by the bounded agent."""
        if not intent:
            return False
        return intent in cls.INTENT_TO_TOOL

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def decide(
        self,
        query: str,
        state: AgentState | None = None,
        plan: IntentPlan | None = None,
    ) -> AgentDecision:
        """Use the existing LifeOS planner to choose the next tool or action."""

        if not query or not query.strip():
            raise ValueError("Query must not be empty.")

        # If previous steps already ran in this execution
        if state is not None and state.tool_calls:
            last_call = state.tool_calls[-1]
            if last_call.error:
                return AgentDecision.fail(
                    f"Previous step failed: {last_call.error}"
                )
            return AgentDecision.finish()

        if plan is None:
            plan = plan_query(query.strip())

        tool_name = self.INTENT_TO_TOOL.get(plan.intent)

        if tool_name is None:
            raise ValueError(
                f"No agent tool registered for intent: {plan.intent}"
            )

        self.registry.get(tool_name)

        if tool_name == "document_search":
            arguments = {
                "query": query.strip(),
            }
        elif tool_name == "relationship_search":
            arguments = plan.arguments.model_dump(exclude_none=True)
            if not arguments.get("entity_name"):
                arguments["entity_name"] = query.strip()
            if "query" not in arguments:
                arguments["query"] = query.strip()
        else:
            arguments = plan.arguments.model_dump(exclude_none=True)
            if "query" not in arguments or not arguments["query"]:
                arguments["query"] = query.strip()

        return AgentDecision(
            action="tool",
            tool_name=tool_name,
            arguments=arguments,
            has_more_steps=False,
        )
