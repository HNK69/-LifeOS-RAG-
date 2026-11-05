from typing import Any

from app.agent.state import AgentState, ToolCall
from app.agent.tools import ToolRegistry
from query.router import QueryResult


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
            state.mark_failed("Maximum agent steps exceeded.")
            return state

        state.mark_running()
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
                state.mark_failed(result.error or "Tool execution failed.")
                self._update_evidence(state, tool_call)
                return state

            self._update_evidence(state, tool_call)
            return state

        except KeyError as exc:
            message = str(exc).strip("'\"")
            tool_call.error = message
            state.mark_failed(message)
            self._update_evidence(state, tool_call)
            return state

        except Exception as exc:
            tool_call.error = str(exc)
            state.mark_failed(str(exc))
            self._update_evidence(state, tool_call)
            return state

    def _update_evidence(
        self,
        state: AgentState,
        tool_call: ToolCall,
    ) -> None:
        """Record structured evidence in state without discarding metadata."""

        if state.evidence is None:
            state.evidence = {
                "query": state.query,
                "tool_calls": [],
                "data": [],
                "sources": [],
            }

        entry: dict[str, Any] = {
            "step": state.step_count,
            "tool_name": tool_call.tool_name,
            "arguments": tool_call.arguments,
            "error": tool_call.error,
        }

        if tool_call.result is not None:
            if isinstance(tool_call.result, QueryResult):
                entry["answer_type"] = tool_call.result.answer_type
                entry["intent"] = (
                    tool_call.result.intent.name
                    if hasattr(tool_call.result.intent, "name")
                    else str(tool_call.result.intent)
                )
                entry["data"] = tool_call.result.data
                state.evidence["data"].append(tool_call.result.data)

                if isinstance(tool_call.result.data, list):
                    for item in tool_call.result.data:
                        if isinstance(item, dict):
                            src = (
                                item.get("source")
                                or item.get("filename")
                                or item.get("path")
                            )
                            if src:
                                state.evidence["sources"].append(str(src))
            else:
                entry["data"] = tool_call.result
                state.evidence["data"].append(tool_call.result)

        state.evidence["tool_calls"].append(entry)
