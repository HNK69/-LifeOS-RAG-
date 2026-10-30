from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field
from query.router import (
    _current_time,
    _document_search,
    _file_discovery,
)


class ToolResult(BaseModel):
    """Structured result returned by an agent tool."""

    success: bool
    data: Any = None
    error: str | None = None


class AgentTool(ABC):
    """Controlled interface between the agent and a LifeOS capability."""

    name: str
    description: str
    input_schema: type[BaseModel]

    @abstractmethod
    def execute(self, input_data: BaseModel) -> ToolResult:
        """Execute the underlying deterministic capability."""
        raise NotImplementedError


class DocumentSearchInput(BaseModel):
    """Validated input for document search."""

    query: str = Field(min_length=1)


class DocumentSearchTool(AgentTool):
    """Agent tool for read-only document retrieval."""

    name = "document_search"
    description = "Search LifeOS documents for relevant content."
    input_schema = DocumentSearchInput

    def execute(self, input_data: DocumentSearchInput) -> ToolResult:
        try:
            validated = self.input_schema.model_validate(input_data)

            if not validated.query.strip():
                return ToolResult(
                    success=False,
                    error="Query must not be empty.",
                )

            result = _document_search(validated.query.strip())

            return ToolResult(
                success=True,
                data=result,
            )

        except Exception as exc:
            return ToolResult(
                success=False,
                error=str(exc),
            )

class CurrentTimeInput(BaseModel):
    """Validated input for current-time queries."""

    query: str = Field(min_length=1)


class CurrentTimeTool(AgentTool):
    """Agent tool for retrieving the current date and time."""

    name = "current_time"
    description = "Get the current date and time."
    input_schema = CurrentTimeInput

    def execute(self, input_data: CurrentTimeInput) -> ToolResult:
        try:
            validated = self.input_schema.model_validate(input_data)

            if not validated.query.strip():
                return ToolResult(
                    success=False,
                    error="Query must not be empty.",
                )

            result = _current_time(validated.query.strip())

            return ToolResult(
                success=True,
                data=result,
            )

        except Exception as exc:
            return ToolResult(
                success=False,
                error=str(exc),
            )

        
class ToolRegistry:
    """Allowlisted collection of tools available to the agent."""

    def __init__(self, tools: list[AgentTool] | None = None):
        self._tools = {tool.name: tool for tool in (tools or [])}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")

        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def list_tools(self) -> list[str]:
        return sorted(self._tools)

def create_default_registry() -> ToolRegistry:
    """Create the initial read-only LifeOS tool registry."""

    return ToolRegistry(
        tools=[
            CurrentTimeTool(),
            DocumentSearchTool(),
            FileDiscoveryTool(),
        ]
    )

class FileDiscoveryInput(BaseModel):
    """Validated input for file discovery."""
    query: str = Field(min_length=1)


class FileDiscoveryTool(AgentTool):
    """Agent tool for read-only file discovery."""
    name = "file_discovery"
    description = "Find relevant LifeOS files."
    input_schema = FileDiscoveryInput

    def execute(self, input_data: FileDiscoveryInput) -> ToolResult:
        try:
            validated = self.input_schema.model_validate(input_data)

            if not validated.query.strip():
                return ToolResult(
                    success=False,
                    error="Query must not be empty.",
                )

            result = _file_discovery(validated.query.strip())

            return ToolResult(
                success=True,
                data=result,
            )

        except Exception as exc:
            return ToolResult(
                success=False,
                error=str(exc),
            )

    