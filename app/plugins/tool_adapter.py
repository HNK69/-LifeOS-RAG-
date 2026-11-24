"""
tool_adapter.py

Dynamic AgentTool adapter for sidecar-exported tools in LifeOS Milestone 9.
Integrates isolated plugin tools into ToolRegistry and AgentExecutor.
"""

from __future__ import annotations

import logging
from typing import Any
from pydantic import BaseModel, create_model

from app.agent.tools import AgentTool, ToolResult
from app.plugins.models import ToolDeclaration

logger = logging.getLogger("lifeos.plugins.tool_adapter")


class SidecarTool(AgentTool):
    """
    Dynamic adapter wrapping a sidecar-exported tool into the LifeOS AgentTool interface.
    """

    def __init__(
        self,
        plugin_id: str,
        declaration: ToolDeclaration,
        invoker_func: Any,
    ):
        self.plugin_id = plugin_id
        self.name = declaration.name
        self.description = declaration.description
        self._invoker = invoker_func

        # Dynamically build a Pydantic schema model from input_schema or fallback to generic
        schema_props = declaration.input_schema.get("properties", {}) if isinstance(declaration.input_schema, dict) else {}
        if schema_props:
            fields = {k: (Any, ...) for k in schema_props.keys()}
            self.input_schema = create_model(f"{self.name.capitalize()}Input", **fields)
        else:
            class GenericSidecarInput(BaseModel):
                query: str | None = None
                arguments: dict[str, Any] | None = None
            self.input_schema = GenericSidecarInput

    def execute(self, input_data: BaseModel) -> ToolResult:
        """
        Execute the tool by delegating across the secure IPC boundary.
        Enforces execution timeouts and returns clean ToolResult without crashing the agent.
        """
        try:
            args = input_data.model_dump() if hasattr(input_data, "model_dump") else dict(input_data)
            res = self._invoker(self.plugin_id, self.name, args)
            return ToolResult(
                success=True,
                data=res,
                error=None,
            )
        except Exception as exc:
            logger.warning(f"Sidecar tool execution error for '{self.name}' ({self.plugin_id}): {exc}")
            return ToolResult(
                success=False,
                data=None,
                error=str(exc),
            )
