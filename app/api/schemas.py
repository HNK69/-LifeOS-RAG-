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
    workspace_id: str | None = Field(
        None,
        max_length=128,
        description="Optional target workspace ID (defaults to resolved request workspace or 'default')",
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
    job_id: str | None = None


class IngestionJobResponse(BaseModel):
    job_id: str
    file_path: str
    status: str
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    error_message: str | None = None
    created_at: str
    updated_at: str
    next_run_at: str | None = None


class IngestionJobCreateResponse(BaseModel):
    job_id: str
    status: str
    filename: str
    message: str


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


# ============================================================
# DEVICE IDENTITY & CRYPTOGRAPHIC ENVELOPE SCHEMAS
# ============================================================

class DeviceIdentityResponse(BaseModel):
    device_id: str
    device_name: str
    device_type: str
    os_info: str
    signing_public_key: str
    exchange_public_key: str
    created_at: str
    last_seen_at: str


class PeerDeviceResponse(BaseModel):
    device_id: str
    device_name: str
    device_type: str
    signing_public_key: str
    exchange_public_key: str
    trust_status: str
    paired_at: str
    last_seen_at: str | None = None
    metadata: dict[str, Any] = {}


class PairingInitiateRequest(BaseModel):
    peer_device_id: str
    ttl_seconds: int = 300


class PairingInitiateResponse(BaseModel):
    challenge_id: str
    peer_device_id: str
    challenge_code: str
    expires_at: str
    created_at: str


class PairingVerifyRequest(BaseModel):
    challenge_id: str
    challenge_code: str
    peer_device_id: str
    peer_device_name: str
    peer_device_type: str
    peer_signing_public_key: str
    peer_exchange_public_key: str
    signature: str


class PairingVerifyResponse(BaseModel):
    success: bool
    message: str
    device_id: str


class SealEnvelopeRequest(BaseModel):
    recipient_device_id: str
    payload: str


class CryptographicEnvelopeSchema(BaseModel):
    version: int
    sender_device_id: str
    recipient_device_id: str
    algorithm: str
    ephemeral_public_key: str
    iv: str
    ciphertext: str
    auth_tag: str
    signature: str
    timestamp: str


class OpenEnvelopeRequest(BaseModel):
    envelope: CryptographicEnvelopeSchema


class OpenEnvelopeResponse(BaseModel):
    success: bool
    payload: str
    sender_device_id: str


# ============================================================
# PRIVACY DASHBOARD, CONSENT & AUDIT SCHEMAS
# ============================================================

class ConsentGrantRequest(BaseModel):
    resource_path: str
    scope_type: str = "folder"
    categories: list[str] = ["document", "spreadsheet", "audio", "image", "structured_data"]
    purposes: list[str] = ["indexing", "retrieval", "reasoning", "local_embeddings"]
    allow_cloud_sync: bool = False
    expires_at: str | None = None


class ConsentGrantResponse(BaseModel):
    consent_id: str
    scope_type: str
    resource_path: str
    categories: list[str]
    purposes: list[str]
    allow_cloud_sync: bool
    granted_at: str
    expires_at: str | None = None
    status: str
    revoked_at: str | None = None
    revocation_reason: str | None = None


class ConsentRevokeRequest(BaseModel):
    reason: str | None = "User requested revocation"


class AuditLogEntryResponse(BaseModel):
    event_id: str
    timestamp: str
    event_type: str
    actor: str
    resource: str
    purpose: str
    status: str
    prev_hash: str
    record_hash: str
    details: dict[str, Any] = {}


class AuditVerifyResponse(BaseModel):
    valid: bool
    verified_records: int
    error: str | None = None


class PrivacySummaryResponse(BaseModel):
    device_id: str
    device_name: str
    local_first_mode: bool = True
    cloud_sync_enabled: bool = False
    telemetry_enabled: bool = False
    active_consented_sources: int
    total_consent_grants: int
    total_audit_events: int
    audit_chain_valid: bool
    total_indexed_documents: int


class PrivacyExportResponse(BaseModel):
    exported_at: str
    device_id: str
    consents: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    context_memories: list[dict[str, Any]]
    people: list[dict[str, Any]]
    audit_log: list[dict[str, Any]]


class PrivacyForgetRequest(BaseModel):
    target_type: str  # 'path', 'person', 'all'
    target_value: str | None = None
    confirm_permanent_purge: bool = False


class PrivacyForgetResponse(BaseModel):
    success: bool
    purged_documents_count: int
    purged_context_count: int
    purged_people_count: int
    message: str


# ============================================================
# WORKSPACE SCHEMAS (Milestone 6)
# ============================================================

class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    profile_type: str = Field("personal", max_length=64)
    description: str | None = Field(None, max_length=512)
    settings: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    profile_type: str | None = Field(None, max_length=64)
    description: str | None = Field(None, max_length=512)
    is_active: bool | None = None
    settings: dict[str, Any] | None = None


class WorkspaceResponse(BaseModel):
    workspace_id: str
    name: str
    profile_type: str
    description: str | None = None
    created_at: str
    updated_at: str
    is_active: bool
    settings: dict[str, Any] = {}
    document_count: int = 0
    context_count: int = 0


class WorkspaceMemberAddRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    role: str = Field("member", pattern="^(owner|admin|member|viewer)$")


class WorkspaceMemberResponse(BaseModel):
    workspace_id: str
    user_id: str
    role: str
    joined_at: str


# ============================================================
# HYBRID ROUTING & PRIVACY GATEKEEPER SCHEMAS (Milestone 7)
# ============================================================

class RoutingPolicyResponse(BaseModel):
    routing_mode: str
    local_llm_url: str
    local_llm_model: str
    daily_budget_usd: float
    pii_redaction_enabled: bool


class RoutingPolicyUpdateRequest(BaseModel):
    routing_mode: str | None = Field(None, pattern="^(local_only|local_first|cloud_with_gatekeeper|hybrid_cascade)$")
    daily_budget_usd: float | None = Field(None, ge=0.01, le=1000.0)
    pii_redaction_enabled: bool | None = None


class GatekeeperInspectRequest(BaseModel):
    text: str = Field(..., min_length=1)
    dry_run: bool = True


class DetectedPIIItem(BaseModel):
    category: str
    start: int
    end: int
    placeholder: str


class GatekeeperInspectResponse(BaseModel):
    entities: list[DetectedPIIItem]
    sensitivity_level: str
    scrubbed_text: str
    cloud_egress_allowed: bool
    reason: str


class RoutingMetricsResponse(BaseModel):
    date: str
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    daily_budget_usd: float
    request_count: int
    is_budget_exceeded: bool


# ============================================================
# CROSS-DEVICE SYNC & CONFLICT SCHEMAS (Milestone 8)
# ============================================================

class SyncExportRequest(BaseModel):
    peer_device_id: str = Field(..., min_length=1)
    workspace_id: str | None = None


class SyncExportResponse(BaseModel):
    envelope: CryptographicEnvelopeSchema
    batch_id: str
    items_count: int
    manifest: dict[str, int]


class SyncImportRequest(BaseModel):
    envelope: CryptographicEnvelopeSchema


class SyncImportResponse(BaseModel):
    session_id: str
    peer_device_id: str
    items_imported: int
    items_skipped: int
    conflicts_detected: int
    conflicts_resolved: int
    success: bool
    timestamp: str


class SyncConflictResponse(BaseModel):
    conflict_id: str
    entity_type: str
    entity_id: str
    workspace_id: str
    peer_device_id: str
    local_data: dict[str, Any]
    remote_data: dict[str, Any]
    local_clock: dict[str, int]
    remote_clock: dict[str, int]
    status: str
    resolution_strategy: str
    resolved_value: dict[str, Any] | None = None
    created_at: str
    resolved_at: str | None = None


class SyncConflictResolveRequest(BaseModel):
    resolved_value: dict[str, Any]


class PeerSyncStatusResponse(BaseModel):
    peer_device_id: str
    device_name: str
    device_type: str
    trust_status: str
    last_synced_at: str | None = None
    unresolved_conflicts: int = 0

