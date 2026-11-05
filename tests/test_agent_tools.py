from unittest.mock import patch

import pytest
from pydantic import BaseModel, ValidationError

from app.agent.tools import (
    AgentTool,
    CurrentTimeInput,
    CurrentTimeTool,
    DocumentSearchInput,
    DocumentSearchTool,
    FileDiscoveryInput,
    FileDiscoveryTool,
    ToolRegistry,
    ToolResult,
    create_default_registry,
)
from query.router import QueryResult


class DummyInput(BaseModel):
    query: str


class DummyTool(AgentTool):
    name = "dummy"
    description = "Test tool"
    input_schema = DummyInput

    def execute(self, input_data: DummyInput) -> ToolResult:
        return ToolResult(
            success=True,
            data=input_data.model_dump(),
        )


class NamedDummyTool(AgentTool):
    def __init__(self, name: str):
        self.name = name
        self.description = f"Dummy tool {name}"
        self.input_schema = DummyInput

    def execute(self, input_data: DummyInput) -> ToolResult:
        return ToolResult(
            success=True,
            data={"tool": self.name, **input_data.model_dump()},
        )


def test_tool_result_success():
    result = ToolResult(success=True, data={"value": 42})

    assert result.success is True
    assert result.data == {"value": 42}
    assert result.error is None


def test_tool_contract():
    tool = DummyTool()
    result = tool.execute(DummyInput(query="hello"))

    assert tool.name == "dummy"
    assert result.success is True
    assert result.data == {"query": "hello"}


def test_document_search_input_rejects_empty_query():
    with pytest.raises(ValidationError):
        DocumentSearchInput(query="")


def test_document_search_tool_rejects_whitespace_query():
    tool = DocumentSearchTool()

    result = tool.execute(DocumentSearchInput(query="   "))

    assert result.success is False
    assert result.error == "Query must not be empty."


def test_document_search_tool_wraps_existing_capability():
    expected = QueryResult(
        query="college notes",
        intent="document_search",
        answer_type="documents",
        data=[{"content": "test document"}],
    )

    with patch("app.agent.tools._document_search", return_value=expected):
        result = DocumentSearchTool().execute(
            DocumentSearchInput(query=" college notes ")
        )

    assert result.success is True
    assert result.data is expected


def test_tool_registry_registers_and_retrieves_tool():
    registry = ToolRegistry()
    tool = DummyTool()

    registry.register(tool)

    assert registry.get("dummy") is tool

    # Verify initialization with tools list also enables retrieval
    init_tool = NamedDummyTool("init_tool")
    registry_with_init = ToolRegistry(tools=[init_tool])
    assert registry_with_init.get("init_tool") is init_tool


def test_tool_registry_rejects_duplicate_registration():
    registry = ToolRegistry()
    registry.register(DummyTool())

    with pytest.raises(ValueError, match="already registered: dummy"):
        registry.register(DummyTool())


def test_tool_registry_returns_sorted_tool_names():
    registry = ToolRegistry(
        tools=[
            NamedDummyTool("zebra_tool"),
            NamedDummyTool("alpha_tool"),
            NamedDummyTool("middle_tool"),
        ]
    )

    assert registry.list_tools() == [
        "alpha_tool",
        "middle_tool",
        "zebra_tool",
    ]


def test_tool_registry_unknown_tool_lookup_raises_expected_error():
    registry = ToolRegistry()

    with pytest.raises(KeyError, match="Unknown tool: non_existent"):
        registry.get("non_existent")


def test_create_default_registry_contains_exactly_intended_tools():
    registry = create_default_registry()

    expected_tools = [
        "current_time",
        "document_search",
        "file_discovery",
    ]

    assert registry.list_tools() == expected_tools
    assert len(registry.list_tools()) == 3

    assert isinstance(registry.get("current_time"), CurrentTimeTool)
    assert isinstance(registry.get("document_search"), DocumentSearchTool)
    assert isinstance(registry.get("file_discovery"), FileDiscoveryTool)


def test_default_registry_tools_have_valid_input_schemas():
    registry = create_default_registry()

    for tool_name in registry.list_tools():
        tool = registry.get(tool_name)

        assert issubclass(tool.input_schema, BaseModel)

        # Ensure valid input succeeds validation
        valid_input = tool.input_schema.model_validate({"query": "valid query"})
        assert valid_input.query == "valid query"

        # Ensure empty input fails schema validation
        with pytest.raises(ValidationError):
            tool.input_schema.model_validate({"query": ""})
