from unittest.mock import patch

from app.agent.tools import (
    AgentTool,
    DocumentSearchInput,
    DocumentSearchTool,
    ToolRegistry,
    ToolResult,
    create_default_registry,
)
from pydantic import BaseModel
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
    try:
        DocumentSearchInput(query="")
        assert False, "Expected validation to fail"
    except ValueError:
        pass


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


def test_tool_registry_registers_and_gets_tool():
    registry = ToolRegistry()

    tool = DummyTool()
    registry.register(tool)

    assert registry.get("dummy") is tool
    assert registry.list_tools() == ["dummy"]


def test_default_registry_contains_document_search():
    registry = create_default_registry()

    assert registry.list_tools() == [
        "current_time",
        "document_search",
        "file_discovery",
    ]
    assert registry.get("document_search").name == "document_search"


def test_tool_registry_rejects_duplicates():
    registry = ToolRegistry()
    registry.register(DummyTool())

    try:
        registry.register(DummyTool())
        assert False, "Expected duplicate registration to fail"
    except ValueError as exc:
        assert "already registered" in str(exc)