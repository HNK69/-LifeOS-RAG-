from typing import Any

from app.agent.state import AgentState, ToolCall
from app.agent.tools import ToolRegistry


class AgentExecutor:
    """Deterministic execution layer for registered LifeOS tools."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def execute_tool(
        self,
        state: AgentState,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AgentState:
        """Validate and execute one registered tool."""

        if state.step_count >= state.max_steps:
            state.status = "failed"
            state.error = "Maximum agent steps exceeded."
            return state

        state.status = "running"
        state.step_count += 1

        tool_call = ToolCall(
            tool_name=tool_name,
            arguments=arguments,
        )

        state.tool_calls.append(tool_call)

        try:
            tool = self.registry.get(tool_name)

            input_data = tool.input_schema.model_validate(arguments)

            result = tool.execute(input_data)

            tool_call.result = result.data

            if not result.success:
                tool_call.error = result.error
                state.status = "failed"
                state.error = result.error
                return state

            return state
        
        except KeyError as exc:
            message = str(exc).strip("'\"")
            tool_call.error = message
            state.status = "failed"
            state.error = message
            return state

        except Exception as exc:
            tool_call.error = str(exc)
            state.status = "failed"
            state.error = str(exc)
            return state