/**
 * types.ts
 * Typed interfaces for LifeOS Desktop Client matching backend API schemas.
 */

export interface SystemHealth {
  status: "ok" | "healthy" | "degraded" | "error";
  service: string;
  version?: string;
  uptime_seconds?: number;
}

export interface ReadinessResponse {
  status: "ready" | "unready";
  databases: Record<string, boolean>;
  storage: Record<string, boolean>;
  identity: boolean;
}

export interface SidecarHandshake {
  event: "sidecar_ready";
  status: "ready";
  host: string;
  port: number;
  token: string;
  pid: number;
}

export interface SidecarState {
  status: "starting" | "ready" | "error" | "stopped";
  host: string;
  port: number;
  token: string;
  pid: number | null;
  error?: string;
}

export interface DeviceIdentity {
  device_id: string;
  device_name: string;
  device_type: string;
  os_info: string;
  signing_public_key: string;
  exchange_public_key: string;
  created_at: string;
  last_seen_at: string;
}

export interface PeerDevice {
  device_id: string;
  device_name: string;
  device_type: string;
  trust_status: "paired" | "revoked";
  created_at?: string;
  paired_at?: string;
  last_seen_at: string | null;
}

export interface PairingInitiateRequest {
  peer_device_id?: string;
  ttl_seconds?: number;
}

export interface PairingInitiateResponse {
  challenge_id: string;
  peer_device_id: string;
  challenge_code: string;
  expires_at: string;
  created_at: string;
  host: string;
  port: number;
  desktop_device_id: string;
  signing_public_key: string;
  exchange_public_key: string;
  pairing_token?: string | null;
}

export interface PairingVerifyRequest {
  challenge_id: string;
  challenge_code: string;
  peer_device_id: string;
  peer_device_name: string;
  peer_device_type: string;
  peer_signing_public_key: string;
  peer_exchange_public_key: string;
  signature: string;
}

export interface PairingVerifyResponse {
  success: boolean;
  message: string;
  device_id: string;
  device_token?: string | null;
}

export interface PeerRevokeResponse {
  revoked: boolean;
  device_id: string;
  status: string;
}

export interface QrPairingPayload {
  version: number;
  challenge_id: string;
  challenge_code: string;
  expires_at: string;
  host: string;
  port: number;
  desktop_device_id: string;
  signing_public_key: string;
  exchange_public_key: string;
  pairing_token?: string | null;
}

export interface PrivacySummary {
  device_id: string;
  device_name: string;
  local_first_mode: boolean;
  cloud_sync_enabled: boolean;
  telemetry_enabled: boolean;
  active_consented_sources: number;
  total_consent_grants: number;
  total_audit_events: number;
  audit_chain_valid: boolean;
  total_indexed_documents: number;
}

export interface ConsentGrant {
  consent_id: string;
  resource_path: string;
  scope_type: "folder" | "file" | "connector" | "plugin";
  status: "active" | "revoked";
  categories: string[];
  purposes: string[];
  allow_cloud_sync: boolean;
  granted_at: string;
  revoked_at?: string;
}

export interface QueryRequest {
  query: string;
  session_id?: string;
  user_id?: string;
  workspace_id?: string;
}

export interface QueryCitation {
  source?: string;
  filename?: string;
  document_id?: string;
  source_path?: string;
  score?: number;
  preview?: string;
  snippet?: string;
}

export interface QueryResponse {
  query?: string;
  answer: string;
  answer_type?: string;
  session_id?: string;
  request_id?: string;
  data?: Array<QueryCitation | string> | Record<string, any> | null;
  sources?: Array<{
    document_id: string;
    source_path: string;
    score: number;
    snippet: string;
  }>;
  confidence?: number;
  routing_strategy?: string;
}

export interface AuditLogEntry {
  event_id: string;
  timestamp: string;
  event_type: string;
  actor: string;
  resource: string;
  purpose: string;
  status: string;
  prev_hash: string;
  record_hash: string;
  details: Record<string, any>;
}

export interface AuditVerifyResponse {
  valid: boolean;
  verified_records: number;
  error: string | null;
}

export interface PrivacyExportResponse {
  exported_at: string;
  device_id: string;
  consents: Array<Record<string, any>>;
  documents: Array<Record<string, any>>;
  context_memories: Array<Record<string, any>>;
  people: Array<Record<string, any>>;
  audit_log: Array<Record<string, any>>;
}

export interface PrivacyForgetRequest {
  target_type: "path" | "person" | "all";
  target_value?: string;
  confirm_permanent_purge: boolean;
}

export interface PrivacyForgetResponse {
  success: boolean;
  purged_documents_count: number;
  purged_context_count: number;
  purged_people_count: number;
  message: string;
}

export interface ConsentRevokeResponse {
  revoked: boolean;
  consent_id: string;
  status?: string;
}

export interface CryptographicEnvelope {
  version: number;
  sender_device_id: string;
  recipient_device_id: string;
  algorithm: string;
  ephemeral_public_key: string;
  iv: string;
  ciphertext: string;
  auth_tag: string;
  signature: string;
  timestamp: string;
}

export interface PeerSyncStatus {
  peer_device_id: string;
  device_name: string;
  device_type: string;
  trust_status: "paired" | "pending" | "revoked" | string;
  last_synced_at: string | null;
  unresolved_conflicts: number;
}

export interface SyncExportRequest {
  peer_device_id: string;
  workspace_id?: string;
}

export interface SyncExportResponse {
  envelope: CryptographicEnvelope;
  batch_id: string;
  items_count: number;
  manifest: Record<string, number>;
}

export interface SyncImportRequest {
  envelope: CryptographicEnvelope;
}

export interface SyncImportResponse {
  session_id: string;
  peer_device_id: string;
  items_imported: number;
  items_skipped: number;
  conflicts_detected: number;
  conflicts_resolved: number;
  success: boolean;
  timestamp: string;
}

export interface SyncConflict {
  conflict_id: string;
  entity_type: string;
  entity_id: string;
  workspace_id: string;
  peer_device_id: string;
  local_data: Record<string, any>;
  remote_data: Record<string, any>;
  local_clock: Record<string, number>;
  remote_clock: Record<string, number>;
  status: "unresolved" | "resolved" | string;
  resolution_strategy: string;
  resolved_value: Record<string, any> | null;
  created_at: string;
  resolved_at: string | null;
}

export interface SyncConflictResolveRequest {
  resolved_value: Record<string, any>;
}

export interface SyncConflictResolveResponse {
  conflict_id: string;
  resolved: boolean;
  status: string;
}

export interface WorkspaceProfile {
  workspace_id: string;
  name: string;
  profile_type: "personal" | "work" | "restricted" | "confidential" | string;
  description: string | null;
  created_at: string;
  updated_at: string;
  is_active: boolean;
  settings: Record<string, any>;
  document_count: number;
  context_count: number;
}

export interface WorkspaceCreateRequest {
  name: string;
  profile_type: string;
  description?: string;
  settings?: Record<string, any>;
}

export interface WorkspaceUpdateRequest {
  name?: string;
  profile_type?: string;
  description?: string;
  is_active?: boolean;
  settings?: Record<string, any>;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  role: "owner" | "admin" | "member" | "viewer" | string;
  joined_at: string;
}

export interface WorkspaceDeleteResponse {
  deleted: boolean;
  workspace_id: string;
}

export interface LifeOSEventPayload {
  event_id: string;
  event_type: string;
  timestamp: string;
  producer: string;
  resource_path?: string;
  payload: Record<string, any>;
}

export interface DiagnosticCheck {
  name: string;
  category: string;
  status: "OK" | "WARN" | "FAIL" | string;
  message: string;
  details?: Record<string, any> | null;
  fix_hint?: string | null;
}

export interface DiagnosticReport {
  checks: DiagnosticCheck[];
  overall_status: "HEALTHY" | "DEGRADED" | "FAILED" | string;
  total_checks: number;
  passed: number;
  warnings: number;
  failed: number;
  timestamp: string;
}

export interface GovernorDiagnostics {
  cpu_percent: number;
  ram_percent: number;
  on_battery: boolean;
  battery_percent: number | null;
  should_pause_ingestion: boolean;
  pause_reason: string | null;
}

export interface UpdateInfo {
  current_version: string;
  latest_version?: string;
  update_available: boolean;
  status: "up_to_date" | "update_available" | "checking" | "error" | "unsupported" | string;
  signature_verified: boolean;
  endpoint?: string;
  release_notes?: string;
  checked_at?: string;
  error?: string;
}

export interface UpdateManifestPlatform {
  signature: string;
  url: string;
  sha256?: string;
}

export interface UpdateManifest {
  version: string;
  notes: string;
  pub_date: string;
  platforms: Record<string, UpdateManifestPlatform>;
}
