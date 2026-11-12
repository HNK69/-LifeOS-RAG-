"""
schemas.py

Pydantic v2 schemas for the LifeOS Phase 11 API.
Strict validation for requests and safe, normalized structures for responses.
"""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================
# QUERY SCHEMAS
# ============================================================

class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Natural language user query to LifeOS",
    )
    session_id: str | None = Field(
        None,
        max_length=128,
        description="Optional session identifier for multi-turn request isolation",
    )
    user_id: str | None = Field(
        None,
        max_length=128,
        description="Optional client-specified user identifier",
    )

    @field_validator("query")
    @classmethod
    def validate_query_not_whitespace(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Query cannot be empty or purely whitespace")
        return v.strip()


class QueryResponse(BaseModel):
    query: str
    answer: str
    answer_type: str
    session_id: str
    request_id: str
    data: Any = None


# ============================================================
# DOCUMENT SCHEMAS
# ============================================================

class DocumentInfo(BaseModel):
    filename: str
    file_size: int | None = None
    file_hash: str | None = None
    last_modified: float | None = None
    status: str | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentInfo]
    count: int


class DocumentUploadResponse(BaseModel):
    filename: str
    status: str
    message: str
    chunks: int | None = None


class DocumentDeleteResponse(BaseModel):
    filename: str
    status: str
    message: str


# ============================================================
# CONTEXT SCHEMAS
# ============================================================

class ContextItem(BaseModel):
    key: str
    value: Any
    context_type: str = "general"
    updated_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None


class ContextSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1, max_length=256)
    value: str = Field(..., min_length=1, max_length=65536)
    context_type: str = Field("general", max_length=64)
    valid_from: str | None = Field(None, max_length=64)
    valid_until: str | None = Field(None, max_length=64)

    @field_validator("key")
    @classmethod
    def validate_key_not_whitespace(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Context key cannot be empty or purely whitespace")
        return v.strip()


class ContextListResponse(BaseModel):
    context: dict[str, Any]
    count: int


class ContextDeleteResponse(BaseModel):
    key: str
    status: str
    message: str


# ============================================================
# PEOPLE SCHEMAS
# ============================================================

class PersonSummary(BaseModel):
    person_id: str
    label: str | None = None
    status: str
    is_user: bool
    created_at: str


class PeopleListResponse(BaseModel):
    people: list[PersonSummary]
    count: int


class PersonConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=256)

    @field_validator("name")
    @classmethod
    def validate_name_not_whitespace(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Person name cannot be empty or purely whitespace")
        return v.strip()


class PersonConfirmResponse(BaseModel):
    person_id: str
    label: str
    status: str


# ============================================================
# REINDEX / ADMIN SCHEMAS
# ============================================================

class ReindexResponse(BaseModel):
    success: bool
    processed_count: int
    error_count: int
    duration_seconds: float


class ReindexStatusResponse(BaseModel):
    is_active: bool
    status: str


# ============================================================
# HEALTH & READINESS SCHEMAS
# ============================================================

class HealthResponse(BaseModel):
    status: str
    timestamp: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool]
    timestamp: str


# ============================================================
# ERROR SCHEMAS
# ============================================================

class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
