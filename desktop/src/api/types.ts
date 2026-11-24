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
  created_at: string;
  last_seen_at: string;
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
  workspace_id?: string;
}

export interface QueryResponse {
  answer: string;
  sources?: Array<{
    document_id: string;
    source_path: string;
    score: number;
    snippet: string;
  }>;
  confidence?: number;
  routing_strategy?: string;
}

export interface LifeOSEventPayload {
  event_id: string;
  event_type: string;
  timestamp: string;
  producer: string;
  resource_path?: string;
  payload: Record<string, any>;
}
