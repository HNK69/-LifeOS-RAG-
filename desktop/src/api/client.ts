/**
 * client.ts
 * Strongly typed LifeOS HTTP Client SDK.
 */

import {
  ConsentGrant,
  DeviceIdentity,
  PeerDevice,
  PrivacySummary,
  QueryRequest,
  QueryResponse,
  ReadinessResponse,
  SystemHealth,
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

  private getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json",
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
    params?: Record<string, string | number | boolean | undefined>
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

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(url, {
        method,
        headers: this.getHeaders(),
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

      return (await response.json()) as T;
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

  // Query & Intelligence
  public async query(req: QueryRequest): Promise<QueryResponse> {
    return this.request<QueryResponse>("POST", "/api/query", req);
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

  public async revokeConsent(consentId: string, reason = "User revoked via desktop client"): Promise<{ revoked: boolean; consent_id: string }> {
    return this.request<{ revoked: boolean; consent_id: string }>(
      "DELETE",
      `/api/privacy/consent/${consentId}`,
      { reason }
    );
  }
}
