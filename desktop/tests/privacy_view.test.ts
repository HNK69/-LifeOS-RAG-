import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LifeOSWebClient } from "../src/api/client";
import { ConsentGrant, PrivacyExportResponse, PrivacySummary } from "../src/api/types";
import { PrivacyView } from "../src/views/PrivacyView";

describe("PrivacyView & Privacy Governance", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const sampleSummary: PrivacySummary = {
    device_id: "dev-local-01",
    device_name: "Workstation",
    local_first_mode: true,
    cloud_sync_enabled: false,
    telemetry_enabled: false,
    active_consented_sources: 2,
    total_consent_grants: 3,
    total_audit_events: 47,
    audit_chain_valid: true,
    total_indexed_documents: 128,
  };

  const sampleConsents: ConsentGrant[] = [
    {
      consent_id: "c-001",
      resource_path: "C:\\Users\\demo\\Documents",
      scope_type: "folder",
      status: "active",
      categories: ["document"],
      purposes: ["indexing", "query"],
      allow_cloud_sync: false,
      granted_at: "2026-11-24T08:00:00Z",
    },
    {
      consent_id: "c-002",
      resource_path: "C:\\Users\\demo\\Temp",
      scope_type: "folder",
      status: "revoked",
      categories: ["document"],
      purposes: ["indexing"],
      allow_cloud_sync: false,
      granted_at: "2026-11-23T08:00:00Z",
      revoked_at: "2026-11-24T09:00:00Z",
    },
  ];

  it("renders privacy posture card, verified audit chain badge, and consented folders table", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const html = renderToStaticMarkup(
      React.createElement(PrivacyView, {
        client,
        initialSummary: sampleSummary,
        initialConsents: sampleConsents,
        initialAuditVerify: { valid: true, verified_records: 47, error: null },
      })
    );

    expect(html).toContain("Local-First (Strict)");
    expect(html).toContain("Disabled (0 bytes)");
    expect(html).toContain("Audit Chain Verified");
    expect(html).toContain("Verified Records: <strong>47</strong>");
    expect(html).toContain("C:\\Users\\demo\\Documents");
    expect(html).toContain("Revoke");
    expect(html).toContain("GDPR Export");
    expect(html).toContain("Cascading Forget...");
  });

  it("renders audit integrity violation state when hash chain fails", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const html = renderToStaticMarkup(
      React.createElement(PrivacyView, {
        client,
        initialSummary: { ...sampleSummary, audit_chain_valid: false },
        initialConsents: [],
        initialAuditVerify: { valid: false, verified_records: 12, error: "Hash mismatch at record 13" },
      })
    );

    expect(html).toContain("Integrity Violation");
    expect(html).toContain("Hash mismatch at record 13");
  });

  it("renders GDPR export summary and Cascading Forget modal", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const exportBundle: PrivacyExportResponse = {
      exported_at: "2026-11-24T15:00:00Z",
      device_id: "dev-local-01",
      consents: [sampleConsents[0]],
      documents: [{ path: "doc1.txt" }],
      context_memories: [],
      people: [{ name: "Alice" }],
      audit_log: [],
    };

    const html = renderToStaticMarkup(
      React.createElement(PrivacyView, {
        client,
        initialSummary: sampleSummary,
        initialConsents: sampleConsents,
        initialExportData: exportBundle,
        initialForgetModalOpen: true,
      })
    );

    expect(html).toContain("GDPR User Data Export Ready");
    expect(html).toContain("Download JSON");
    expect(html).toContain("Cascading Forget (Right to be Forgotten)");
    expect(html).toContain("Execute Permanent Purge");
  });

  it("invokes audit verify, consent revoke, GDPR export, and cascading forget endpoints", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ valid: true, verified_records: 50, error: null }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ revoked: true, consent_id: "c-001", status: "revoked" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          exported_at: "2026-11-24T16:00:00Z",
          device_id: "dev-local-01",
          consents: [],
          documents: [],
          context_memories: [],
          people: [],
          audit_log: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          purged_documents_count: 3,
          purged_context_count: 1,
          purged_people_count: 0,
          message: "Purged 3 documents",
        }),
      });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000", authToken: "admin-tok" });

    const verifyRes = await client.verifyAuditTrail();
    expect(verifyRes.valid).toBe(true);
    expect(verifyRes.verified_records).toBe(50);

    const revokeRes = await client.revokeConsent("c-001", "User clicked revoke");
    expect(revokeRes.revoked).toBe(true);

    const exportRes = await client.exportUserData();
    expect(exportRes.device_id).toBe("dev-local-01");

    const forgetRes = await client.forgetUserData({
      target_type: "path",
      target_value: "C:\\Users\\demo\\Private",
      confirm_permanent_purge: true,
    });
    expect(forgetRes.success).toBe(true);
    expect(forgetRes.purged_documents_count).toBe(3);

    expect(mockFetch).toHaveBeenCalledTimes(4);
    expect(mockFetch.mock.calls[0][0]).toBe("http://127.0.0.1:8000/api/privacy/audit/verify");
    expect(mockFetch.mock.calls[1][0]).toBe("http://127.0.0.1:8000/api/privacy/consent/c-001");
    expect(mockFetch.mock.calls[1][1].method).toBe("DELETE");
    expect(mockFetch.mock.calls[2][0]).toBe("http://127.0.0.1:8000/api/privacy/export");
    expect(mockFetch.mock.calls[3][0]).toBe("http://127.0.0.1:8000/api/privacy/forget");
  });
});
