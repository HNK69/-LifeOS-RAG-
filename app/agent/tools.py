from abc import ABC, abstractmethod
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from query.router import (
    _current_time,
    _document_search,
    _file_discovery,
    _relationship_search,
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
            RelationshipSearchTool(),
        ]
    )


class RelationshipSearchInput(BaseModel):
    """Validated input for relationship retrieval."""

    query: str | None = None
    entity_name: str | None = None
    entity_type: str | None = None
    relationship_type: str | None = None
    direction: Literal["outgoing", "incoming", "both"] = "both"

    @model_validator(mode="after")
    def validate_entity_identifier(self):
        if not self.entity_name and self.query:
            self.entity_name = self.query
        if not self.entity_name or not self.entity_name.strip():
            raise ValueError("Entity name or query must not be empty.")
        return self


class RelationshipSearchTool(AgentTool):
    """Agent tool for deterministic entity relationship retrieval."""

    name = "relationship_search"
    description = "Find relationships and connections for a named entity."
    input_schema = RelationshipSearchInput

    def execute(self, input_data: RelationshipSearchInput) -> ToolResult:
        try:
            validated = self.input_schema.model_validate(input_data)
            entity_name = (
                validated.entity_name.strip()
                if validated.entity_name
                else ""
            )

            if not entity_name:
                return ToolResult(
                    success=False,
                    error="Entity name must not be empty.",
                )

            args = {
                "entity_name": entity_name,
                "entity_type": validated.entity_type,
                "relationship_type": validated.relationship_type,
                "direction": validated.direction,
            }
            args = {k: v for k, v in args.items() if v is not None}

            result = _relationship_search(
                query=validated.query or f"relationship search for {entity_name}",
                arguments=args,
            )

            return ToolResult(
                success=True,
                data=result,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=str(exc),
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

    