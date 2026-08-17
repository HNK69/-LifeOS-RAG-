from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


IntentName = Literal[
    "file_discovery", "document_search", "structured_discovery",
    "structured_query", "schedule_query", "current_time", "unknown",
]


class StructuredArguments(BaseModel):
    dataset_query: str | None = None
    operation: Literal["count", "aggregate", "filter", "sort"] | None = None
    column: str | None = None
    value: Any = None
    filters: dict[str, Any] = Field(default_factory=dict)
    aggregation: Literal["sum", "avg", "min", "max"] | None = None
    descending: bool = False
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("operation", mode="before")
    @classmethod
    def normalize_operation(cls, value):
        aliases = {
            "average": "aggregate",
            "mean": "aggregate",
            "maximum": "aggregate",
            "minimum": "aggregate",
            "total": "aggregate",
            "sum": "aggregate",
            "avg": "aggregate",
            "min": "aggregate",
            "max": "aggregate",
            "how_many": "count",
            "number": "count",
            "count_rows": "count",
        }
        return aliases.get(str(value).lower(), value) if value else value

    @field_validator("aggregation", mode="before")
    @classmethod
    def normalize_aggregation(cls, value):
        aliases = {
            "average": "avg",
            "mean": "avg",
            "maximum": "max",
            "minimum": "min",
            "total": "sum",
        }
        return aliases.get(str(value).lower(), value) if value else value


class IntentPlan(BaseModel):
    intent: IntentName
    confidence: float = Field(ge=0.0, le=1.0)
    arguments: StructuredArguments = Field(default_factory=StructuredArguments)
