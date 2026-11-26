import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LifeOSWebClient } from "../src/api/client";
import { PeerSyncStatus, SyncConflict, SyncExportResponse } from "../src/api/types";
import { SyncView, computeDataDiff } from "../src/views/SyncView";

describe("SyncView & Conflict Resolution", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const samplePeers: PeerSyncStatus[] = [
    {
      peer_device_id: "peer-phone-01",
      device_name: "Pixel 9 Companion",
      device_type: "mobile",
      trust_status: "paired",
      last_synced_at: "2026-11-24T14:30:00Z",
      unresolved_conflicts: 1,
    },
    {
      peer_device_id: "peer-old-02",
      device_name: "Old Tablet",
      device_type: "tablet",
      trust_status: "revoked",
      last_synced_at: null,
      unresolved_conflicts: 0,
    },
  ];

  const sampleConflicts: SyncConflict[] = [
    {
      conflict_id: "conf-101",
      entity_type: "context",
      entity_id: "project_deadline",
      workspace_id: "default",
      peer_device_id: "peer-phone-01",
      local_data: { value: "2026-12-01", priority: "high" },
      remote_data: { value: "2026-12-15", priority: "high" },
      local_clock: { desktop: 4, mobile: 2 },
      remote_clock: { desktop: 3, mobile: 5 },
      status: "unresolved",
      resolution_strategy: "MANUAL",
      resolved_value: null,
      created_at: "2026-11-24T14:31:00Z",
      resolved_at: null,
    },
  ];

  it("renders paired devices with status badges and side-by-side manual conflict diff viewer", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const exportPreview: SyncExportResponse = {
      batch_id: "batch-001",
      items_count: 5,
      manifest: { context: 5 },
      envelope: {
        version: 1,
        sender_device_id: "desktop-01",
        recipient_device_id: "peer-phone-01",
        algorithm: "X25519-HKDF-AES256GCM",
        ephemeral_public_key: "epk",
        iv: "iv",
        ciphertext: "ct",
        auth_tag: "tag",
        signature: "sig",
        timestamp: "2026-11-24T14:45:00Z",
      },
    };

    const html = renderToStaticMarkup(
      React.createElement(SyncView, {
        client,
        activeWorkspaceId: "default",
        initialPeers: samplePeers,
        initialConflicts: sampleConflicts,
        initialExportResult: exportPreview,
      })
    );

    expect(html).toContain("Pixel 9 Companion");
    expect(html).toContain("Trusted / Paired");
    expect(html).toContain("Old Tablet");
    expect(html).toContain("Revoked");
    expect(html).toContain("1 conflict");
    expect(html).toContain("Sealed Package Ready (Batch: batch-001)");
    expect(html).toContain("X25519-HKDF-AES256GCM");
    expect(html).toContain("Manual Conflict Resolution (1 unresolved)");
    expect(html).toContain("project_deadline");
    expect(html).toContain("2026-12-01");
    expect(html).toContain("2026-12-15");
    expect(html).toContain("Keep Local");
    expect(html).toContain("Accept Remote");
    expect(html).toContain("Custom Merge");
  });

  it("computes field-level differences accurately in computeDataDiff", () => {
    const rows = computeDataDiff(
      { value: "local-val", shared: "same", localOnly: 42 },
      { value: "remote-val", shared: "same", remoteOnly: true }
    );

    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(byKey.shared.isDifferent).toBe(false);
    expect(byKey.value.isDifferent).toBe(true);
    expect(byKey.localOnly.remoteVal).toBe("—");
    expect(byKey.remoteOnly.localVal).toBe("—");
  });

  it("calls sync peers, export, import, conflicts, and resolve API endpoints", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => samplePeers })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          batch_id: "b1",
          items_count: 1,
          manifest: { context: 1 },
          envelope: {
            version: 1,
            sender_device_id: "d1",
            recipient_device_id: "peer-phone-01",
            algorithm: "X25519-HKDF-AES256GCM",
            ephemeral_public_key: "e",
            iv: "i",
            ciphertext: "c",
            auth_tag: "a",
            signature: "s",
            timestamp: "2026-11-24T15:00:00Z",
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          session_id: "s1",
          peer_device_id: "peer-phone-01",
          items_imported: 2,
          items_skipped: 0,
          conflicts_detected: 1,
          conflicts_resolved: 0,
          success: true,
          timestamp: "2026-11-24T15:01:00Z",
        }),
      })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => sampleConflicts })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ conflict_id: "conf-101", resolved: true, status: "resolved" }),
      });

    globalThis.fetch = mockFetch;
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });

    const peers = await client.listSyncPeers();
    expect(peers).toHaveLength(2);

    const exported = await client.exportSyncPackage({ peer_device_id: "peer-phone-01" });
    expect(exported.batch_id).toBe("b1");

    const imported = await client.importSyncPackage({ envelope: exported.envelope });
    expect(imported.items_imported).toBe(2);

    const conflicts = await client.listSyncConflicts("unresolved");
    expect(conflicts).toHaveLength(1);

    const resolved = await client.resolveSyncConflict("conf-101", { value: "2026-12-15", priority: "high" });
    expect(resolved.resolved).toBe(true);
    expect(mockFetch.mock.calls[4][0]).toBe("http://127.0.0.1:8000/api/sync/conflicts/conf-101/resolve");
    expect(JSON.parse(mockFetch.mock.calls[4][1].body)).toEqual({
      resolved_value: { value: "2026-12-15", priority: "high" },
    });
  });
});
