from typing import Any

from pydantic import BaseModel, Field

from intelligence.planner import plan_query
from app.agent.tools import ToolRegistry


class AgentDecision(BaseModel):
    """Validated decision describing the next tool invocation."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentPlanner:
    """Translate the existing LifeOS intent planner into an agent decision."""

    INTENT_TO_TOOL = {
        "current_time": "current_time",
        "document_search": "document_search",
        "file_discovery": "file_discovery",
    }

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def decide(self, query: str) -> AgentDecision:
        """Use the existing LifeOS planner to choose the next tool."""

        if not query or not query.strip():
            raise ValueError("Query must not be empty.")

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
        else:
            arguments = plan.arguments.model_dump(exclude_none=True)

        return AgentDecision(
            tool_name=tool_name,
            arguments=arguments,
        )