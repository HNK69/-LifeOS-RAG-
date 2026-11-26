/**
 * client.ts
 * Strongly typed LifeOS HTTP Client SDK.
 */

import {
  AuditLogEntry,
  AuditVerifyResponse,
  ConsentGrant,
  ConsentRevokeResponse,
  DeviceIdentity,
  DiagnosticReport,
  GovernorDiagnostics,
  PairingInitiateRequest,
  PairingInitiateResponse,
  PairingVerifyRequest,
  PairingVerifyResponse,
  PeerDevice,
  PeerRevokeResponse,
  PeerSyncStatus,
  PrivacyExportResponse,
  PrivacyForgetRequest,
  PrivacyForgetResponse,
  PrivacySummary,
  QueryRequest,
  QueryResponse,
  ReadinessResponse,
  SyncConflict,
  SyncConflictResolveResponse,
  SyncExportRequest,
  SyncExportResponse,
  SyncImportRequest,
  SyncImportResponse,
  SystemHealth,
  WorkspaceCreateRequest,
  WorkspaceDeleteResponse,
  WorkspaceMember,
  WorkspaceProfile,
  WorkspaceUpdateRequest,
} from "./types";

export class LifeOSApiError extends Error {
  public statusCode: number;
  public responseBody: string;

  constructor(message: string, statusCode: number, responseBody: string = "") {
    super(`[${statusCode}] ${message}${responseBody ? `: ${responseBody}` : ""}`);
    this.name = "LifeOSApiError";
    this.statusCode = statusCode;
    this.responseBody = responseBody;
  }
}

export interface ClientConfig {
  baseUrl: string;
  authToken?: string;
  timeoutMs?: number;
}

export class LifeOSWebClient {
  private baseUrl: string;
  private authToken?: string;
  private timeoutMs: number;

  constructor(config: ClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.authToken = config.authToken;
    this.timeoutMs = config.timeoutMs ?? 15000;
  }

  public setAuthToken(token: string): void {
    this.authToken = token;
  }

  public setBaseUrl(url: string): void {
    this.baseUrl = url.replace(/\/+$/, "");
  }

  public getBaseUrl(): string {
    return this.baseUrl;
  }

  private getHeaders(extraHeaders?: Record<string, string>): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(extraHeaders || {}),
    };
    if (this.authToken) {
      headers["Authorization"] = `Bearer ${this.authToken}`;
    }
    return headers;
  }

  public async request<T>(
    method: string,
    path: string,
    body?: unknown,
    params?: Record<string, string | number | boolean | undefined>,
    extraHeaders?: Record<string, string>,
    timeoutOverrideMs?: number
  ): Promise<T> {
    let url = `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
    if (params) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined) {
          q.append(k, String(v));
        }
      }
      const qs = q.toString();
      if (qs) {
        url = `${url}?${qs}`;
      }
    }

    const effectiveTimeout = timeoutOverrideMs ?? this.timeoutMs;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), effectiveTimeout);

    try {
      const response = await fetch(url, {
        method,
        headers: this.getHeaders(extraHeaders),
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      clearTimeout(timer);

      if (!response.ok) {
        let errText = "";
        try {
          errText = await response.text();
        } catch {
          // ignore
        }
        throw new LifeOSApiError(response.statusText || "Request failed", response.status, errText);
      }

      if (response.status === 204) {
        return {} as T;
      }

      try {
        return (await response.json()) as T;
      } catch (parseErr: any) {
        if (parseErr instanceof SyntaxError || parseErr?.name === "SyntaxError") {
          throw new LifeOSApiError(
            parseErr.message || "Invalid JSON response",
            response.status,
            ""
          );
        }
        throw parseErr;
      }
    } catch (err: any) {
      clearTimeout(timer);
      if (err instanceof LifeOSApiError) {
        throw err;
      }
      throw new LifeOSApiError(err?.message || "Network error", 0, "");
    }
  }

  // System & Health
  public async getHealth(): Promise<SystemHealth> {
    return this.request<SystemHealth>("GET", "/health");
  }

  public async getReady(): Promise<ReadinessResponse> {
    return this.request<ReadinessResponse>("GET", "/ready");
  }

  // Device & Identity
  public async getDeviceIdentity(): Promise<DeviceIdentity> {
    return this.request<DeviceIdentity>("GET", "/api/device/identity");
  }

  public async listPeers(): Promise<PeerDevice[]> {
    return this.request<PeerDevice[]>("GET", "/api/device/peers");
  }

  public async initiatePairing(req: PairingInitiateRequest = {}): Promise<PairingInitiateResponse> {
    return this.request<PairingInitiateResponse>("POST", "/api/device/pairing/initiate", {
      peer_device_id: req.peer_device_id ?? "*",
      ttl_seconds: req.ttl_seconds ?? 300,
    });
  }

  public async verifyPairing(req: PairingVerifyRequest): Promise<PairingVerifyResponse> {
    return this.request<PairingVerifyResponse>("POST", "/api/device/pairing/verify", req);
  }

  public async revokePeer(peerId: string): Promise<PeerRevokeResponse> {
    return this.request<PeerRevokeResponse>("DELETE", `/api/device/peers/${encodeURIComponent(peerId)}`);
  }

  // Query & Intelligence
  public async query(req: QueryRequest, timeoutOverrideMs: number = 30000): Promise<QueryResponse> {
    const headers: Record<string, string> | undefined = req.workspace_id
      ? { "X-Workspace-ID": req.workspace_id }
      : undefined;
    return this.request<QueryResponse>("POST", "/api/query", req, undefined, headers, timeoutOverrideMs);
  }

  // Privacy & Consent
  public async getPrivacySummary(): Promise<PrivacySummary> {
    return this.request<PrivacySummary>("GET", "/api/privacy/summary");
  }

  public async listConsents(status?: "active" | "revoked"): Promise<ConsentGrant[]> {
    return this.request<ConsentGrant[]>("GET", "/api/privacy/consent", undefined, { status });
  }

  public async grantConsent(
    resourcePath: string,
    scopeType: "folder" | "file" | "connector" | "plugin" = "folder",
    categories?: string[],
    purposes?: string[]
  ): Promise<ConsentGrant> {
    return this.request<ConsentGrant>("POST", "/api/privacy/consent", {
      resource_path: resourcePath,
      scope_type: scopeType,
      categories: categories || ["general"],
      purposes: purposes || ["indexing", "query"],
    });
  }

  public async revokeConsent(consentId: string, reason = "User revoked via desktop client"): Promise<ConsentRevokeResponse> {
    return this.request<ConsentRevokeResponse>(
      "DELETE",
      `/api/privacy/consent/${encodeURIComponent(consentId)}`,
      { reason }
    );
  }

  public async getAuditTrail(params?: {
    event_type?: string;
    actor?: string;
    limit?: number;
    offset?: number;
  }): Promise<AuditLogEntry[]> {
    return this.request<AuditLogEntry[]>("GET", "/api/privacy/audit", undefined, params);
  }

  public async verifyAuditTrail(): Promise<AuditVerifyResponse> {
    return this.request<AuditVerifyResponse>("GET", "/api/privacy/audit/verify");
  }

  public async exportUserData(): Promise<PrivacyExportResponse> {
    return this.request<PrivacyExportResponse>("POST", "/api/privacy/export");
  }

  public async forgetUserData(req: PrivacyForgetRequest): Promise<PrivacyForgetResponse> {
    return this.request<PrivacyForgetResponse>("POST", "/api/privacy/forget", req);
  }

  // Cross-Device Sync & Conflicts
  public async listSyncPeers(): Promise<PeerSyncStatus[]> {
    return this.request<PeerSyncStatus[]>("GET", "/api/sync/peers");
  }

  public async exportSyncPackage(req: SyncExportRequest): Promise<SyncExportResponse> {
    return this.request<SyncExportResponse>("POST", "/api/sync/export", req);
  }

  public async importSyncPackage(req: SyncImportRequest): Promise<SyncImportResponse> {
    return this.request<SyncImportResponse>("POST", "/api/sync/import", req);
  }

  public async listSyncConflicts(
    status?: "unresolved" | "resolved",
    workspaceId?: string
  ): Promise<SyncConflict[]> {
    return this.request<SyncConflict[]>("GET", "/api/sync/conflicts", undefined, {
      status,
      workspace_id: workspaceId,
    });
  }

  public async resolveSyncConflict(
    conflictId: string,
    resolvedValue: Record<string, any>
  ): Promise<SyncConflictResolveResponse> {
    return this.request<SyncConflictResolveResponse>(
      "POST",
      `/api/sync/conflicts/${encodeURIComponent(conflictId)}/resolve`,
      { resolved_value: resolvedValue }
    );
  }

  // Workspaces
  public async listWorkspaces(): Promise<WorkspaceProfile[]> {
    return this.request<WorkspaceProfile[]>("GET", "/api/workspaces");
  }

  public async getWorkspace(workspaceId: string): Promise<WorkspaceProfile> {
    return this.request<WorkspaceProfile>("GET", `/api/workspaces/${encodeURIComponent(workspaceId)}`);
  }

  public async createWorkspace(req: WorkspaceCreateRequest): Promise<WorkspaceProfile> {
    return this.request<WorkspaceProfile>("POST", "/api/workspaces", req);
  }

  public async updateWorkspace(
    workspaceId: string,
    req: WorkspaceUpdateRequest
  ): Promise<WorkspaceProfile> {
    return this.request<WorkspaceProfile>("PUT", `/api/workspaces/${encodeURIComponent(workspaceId)}`, req);
  }

  public async deleteWorkspace(workspaceId: string): Promise<WorkspaceDeleteResponse> {
    return this.request<WorkspaceDeleteResponse>("DELETE", `/api/workspaces/${encodeURIComponent(workspaceId)}`);
  }

  public async listWorkspaceMembers(workspaceId: string): Promise<WorkspaceMember[]> {
    return this.request<WorkspaceMember[]>("GET", `/api/workspaces/${encodeURIComponent(workspaceId)}/members`);
  }

  // Diagnostics & Resource Governor (Milestone C4)
  public async getDiagnostics(strict: boolean = false): Promise<DiagnosticReport> {
    return this.request<DiagnosticReport>("GET", "/api/diagnostics", undefined, { strict });
  }

  public async getGovernorDiagnostics(): Promise<GovernorDiagnostics> {
    return this.request<GovernorDiagnostics>("GET", "/api/diagnostics/governor");
  }
}
